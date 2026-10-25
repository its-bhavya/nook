"""
Neon Postgres integration.

Uses the pooled connection string (DATABASE_URL, hostname with `-pooler`) for
application traffic and the direct string (DATABASE_URL_UNPOOLED) for schema
creation — per the neon-postgres best practices.

Driver: psycopg 3. On Windows psycopg's *async* mode cannot run on the default
ProactorEventLoop, so we use psycopg's **sync** API and run every DB call
through ``asyncio.to_thread``. This keeps an async interface for callers,
never blocks the event loop, and works on any event loop / platform. The sync
``ConnectionPool`` is used over the pooled (PgBouncer) endpoint with
client-side prepared-statement caching disabled (transaction-mode safe).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.config import get_settings
from app.models import JobEvent, JobKind, JobStatus, JobStatusEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync pool (pooled connection for app traffic)
# ---------------------------------------------------------------------------

_sync_pool: Optional[ConnectionPool] = None
_schema_initialized = False


def _pool_conninfo() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set — run `neon deploy`/`neon env pull`.")
    return url


def _configure(conn) -> None:
    # PgBouncer transaction mode may route the next transaction to a different
    # backend, so don't create server-side prepared statements (the default
    # prepare_threshold=5 would, and break with "prepared statement does not exist").
    conn.prepare_threshold = 0


def _get_sync_pool() -> ConnectionPool:
    global _sync_pool
    if _sync_pool is not None:
        return _sync_pool
    _sync_pool = ConnectionPool(
        conninfo=_pool_conninfo(),
        min_size=1,
        max_size=5,
        timeout=30,            # tolerate Neon cold starts after scale-to-zero
        open=False,
        configure=_configure,
        # autocommit keeps PgBouncer transaction mode happy.
        kwargs={"autocommit": True, "connect_timeout": 30},
    )
    _sync_pool.open(wait=True)
    logger.info("Neon Postgres sync pool opened")
    return _sync_pool


# ---------------------------------------------------------------------------
# Schema (direct connection, run once)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id     text PRIMARY KEY,
    kind       text NOT NULL,
    status     text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    events     jsonb   NOT NULL DEFAULT '[]'::jsonb,
    result     jsonb
);
"""

_UPSERT_SQL = """
INSERT INTO jobs (job_id, kind, status, updated_at, events, result)
VALUES (%s, %s, %s, now(), %s, %s)
ON CONFLICT (job_id) DO UPDATE
   SET status     = EXCLUDED.status,
       updated_at = now(),
       events     = EXCLUDED.events,
       result     = EXCLUDED.result
"""

_SELECT_SQL = """
SELECT job_id, kind, status, events, result
FROM jobs
WHERE job_id = %s
"""


def _sync_init_schema() -> None:
    global _schema_initialized
    if _schema_initialized:
        return
    dsn = get_settings().database_url_unpooled
    if not dsn:
        raise RuntimeError("DATABASE_URL_UNPOOLED is not set — run `neon deploy`/`neon env pull`.")
    with psycopg.Connection.connect(dsn, autocommit=True, connect_timeout=30) as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA_SQL)
    _schema_initialized = True
    logger.info("Neon Postgres schema ensured (jobs table)")


def _sync_ping() -> bool:
    try:
        pool = _get_sync_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
                return bool(row and row[0] == 1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neon Postgres ping failed: %s", exc)
        return False


def _events_json(job: JobStatus):
    return Jsonb([e.model_dump(mode="json") for e in job.events])


def _result_json(job: JobStatus):
    return Jsonb(job.result) if job.result is not None else None


def _sync_save_job(job: JobStatus) -> None:
    pool = _get_sync_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                _UPSERT_SQL,
                (job.job_id, job.kind.value, job.status.value, _events_json(job), _result_json(job)),
            )


def _sync_load_job(job_id: str) -> Optional[JobStatus]:
    pool = _get_sync_pool()
    with pool.connection() as conn:
        # Scope dict_row to this cursor so we don't mutate the shared pooled connection.
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_SELECT_SQL, (job_id,))
            row = cur.fetchone()
    if row is None:
        return None
    events = [JobEvent(**e) for e in (row["events"] or [])]
    return JobStatus(
        job_id=row["job_id"],
        kind=JobKind(row["kind"]),
        status=JobStatusEnum(row["status"]),
        events=events,
        result=row["result"],
    )


def _sync_count_jobs() -> int:
    pool = _get_sync_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM jobs")
            row = cur.fetchone()
            return int(row[0]) if row else 0


def _sync_close_pool() -> None:
    global _sync_pool
    if _sync_pool is not None:
        _sync_pool.close()
        _sync_pool = None


# ---------------------------------------------------------------------------
# Async interface (wraps the sync calls in a worker thread)
# ---------------------------------------------------------------------------

async def init_schema() -> None:
    await asyncio.to_thread(_sync_init_schema)


async def ping() -> bool:
    return await asyncio.to_thread(_sync_ping)


async def save_job(job: JobStatus) -> None:
    await asyncio.to_thread(_sync_save_job, job)


async def load_job(job_id: str) -> Optional[JobStatus]:
    return await asyncio.to_thread(_sync_load_job, job_id)


async def count_jobs() -> int:
    return await asyncio.to_thread(_sync_count_jobs)


async def close_pool() -> None:
    await asyncio.to_thread(_sync_close_pool)
