"""
End-to-end verification of the three Neon services wired into Nook.

Run:  .venv\\Scripts\\python.exe scripts\\verify_neon.py

Tests:
  1. Postgres    — schema init, ping, insert+load a job row, count rows.
  2. Storage     — bucket reachable, put+get a JSON object round trip.
  3. AI Gateway  — config status (and live /v1/models list when configured).

Exit code 0 when every *configured* service passes; 1 if any configured
service fails. The AI Gateway is reported as "not configured" (skipped) on
the Free plan rather than a failure.
"""
from __future__ import annotations

import os
import sys

# Ensure the project root is importable when run as `python scripts/verify_neon.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import uuid
from datetime import datetime, timezone

import app.ai_gateway as ai_gateway
import app.db as db
import app.storage as storage
from app.config import get_settings
from app.models import JobEvent, JobKind, JobStatus, JobStatusEnum


def stamp(msg: str) -> None:
    print(f"[verify] {msg}", flush=True)


async def test_postgres() -> bool:
    stamp("== Postgres ==")
    s = get_settings()
    if not s.database_url:
        stamp("  SKIP: DATABASE_URL not set")
        return None  # type: ignore[return-value]
    try:
        await db.init_schema()
        stamp("  schema init OK (jobs table)")
    except Exception as exc:  # noqa: BLE001
        stamp(f"  FAIL schema init: {exc}")
        return False

    ok = await db.ping()
    stamp(f"  ping (SELECT 1): {'OK' if ok else 'FAIL'}")
    if not ok:
        return False

    # Insert a job, then read it straight back from the database.
    jid = f"verify-{uuid.uuid4().hex[:8]}"
    job = JobStatus(
        job_id=jid,
        kind=JobKind.discover,
        status=JobStatusEnum.done,
        events=[JobEvent(message="verified-by-script", timestamp=datetime.now(timezone.utc).isoformat())],
        result={"hello": "neon", "n": 42, "list": [1, 2, 3]},
    )
    await db.save_job(job)
    loaded = await db.load_job(jid)
    ok2 = (
        loaded is not None
        and loaded.job_id == jid
        and loaded.status == JobStatusEnum.done
        and loaded.result == {"hello": "neon", "n": 42, "list": [1, 2, 3]}
        and len(loaded.events) == 1
    )
    stamp(f"  insert+load round trip: {'OK' if ok2 else 'FAIL'}")
    count = await db.count_jobs()
    stamp(f"  jobs table rows now: {count}")
    await db.close_pool()
    return ok2


def test_storage() -> bool:
    stamp("== Object Storage ==")
    if not storage.is_configured():
        stamp("  SKIP: AWS_* not set")
        return None  # type: ignore[return-value]
    bucket = storage.bucket_name()
    if not storage.head_bucket():
        stamp(f"  FAIL: bucket '{bucket}' not reachable")
        return False
    stamp(f"  bucket reachable: {bucket}")

    key = f"verify/{uuid.uuid4().hex[:8]}.json"
    payload = {"verified": True, "ts": datetime.now(timezone.utc).isoformat(), "source": "verify_neon.py"}
    storage.put_json(key, payload)
    back = storage.get_json(key)
    ok = back == payload
    stamp(f"  put+get round trip: {'OK' if ok else 'FAIL'}")
    exists = storage.object_exists(key)
    stamp(f"  object exists (HEAD): {exists}")
    return ok and exists


async def test_ai_gateway():
    stamp("== AI Gateway ==")
    if not ai_gateway.is_configured():
        stamp("  NOT CONFIGURED: NEON_AI_GATEWAY_* unset (expected on the Free plan)")
        stamp("  -> set aiGateway: true in neon.ts on a paid plan, then `neon deploy`")
        return None
    try:
        models = await ai_gateway.list_models()
        ids = models.get("models", [])
        stamp(f"  list_models OK: {len(ids)} models served by this branch")
        stamp(f"  sample: {ids[:5]}")
        return True
    except Exception as exc:  # noqa: BLE001
        stamp(f"  FAIL list_models: {exc}")
        return False


async def main() -> None:
    results = {
        "postgres": await test_postgres(),
        "storage": test_storage(),
        "ai_gateway": await test_ai_gateway(),
    }
    stamp("== Summary ==")
    for k, v in results.items():
        stamp(f"  {k}: {v}")
    failed = [k for k, v in results.items() if v is False]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
