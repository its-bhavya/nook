"""
In-memory job store and background task runners.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models import JobEvent, JobKind, JobStatus, JobStatusEnum

logger = logging.getLogger(__name__)

_store: dict[str, JobStatus] = {}
JOB_TIMEOUT = 300


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(kind: JobKind) -> JobStatus:
    job = JobStatus(
        job_id=str(uuid.uuid4()),
        kind=kind,
        status=JobStatusEnum.queued,
        events=[],
        result=None,
    )
    _store[job.job_id] = job
    return job


def get_job(job_id: str) -> JobStatus | None:
    return _store.get(job_id)


def emit(job_id: str, stage: str, message: str) -> None:
    job = _store.get(job_id)
    if job is None:
        return
    job.events.append(JobEvent(message=f"[{stage}] {message}", timestamp=_now_iso()))


def add_event(job_id: str, message: str) -> None:
    job = _store.get(job_id)
    if job is None:
        return
    job.events.append(JobEvent(message=message, timestamp=_now_iso()))


def set_running(job_id: str) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.running


def set_done(job_id: str, result: Any) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.done
        job.result = result


def set_done_cached(job_id: str, result: Any, generated_at: str) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.done
        job.result = result
        job.from_cache = True
        job.generated_at = generated_at


def set_done_mock(job_id: str, result: Any) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.done
        job.result = result
        job.mock = True


def set_failed(job_id: str, error: str) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.failed
        job.error = error
        job.events.append(JobEvent(message=f"ERROR: {error}", timestamp=_now_iso()))


# ---------------------------------------------------------------------------
# Background task: discover
# ---------------------------------------------------------------------------

async def run_discover_job(job_id: str, profile: Any, query: str | None) -> None:
    from app.discover import discover_issues
    from app.rank import rank_candidates

    set_running(job_id)

    def _on_event(msg: str) -> None:
        emit(job_id, "discover", msg)

    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            emit(job_id, "discover", "Starting issue discovery")
            candidates = await discover_issues(profile, query, on_event=_on_event)

            emit(job_id, "rank", f"Ranking {len(candidates)} candidates")
            shortlist = await rank_candidates(profile, candidates)

            set_done(job_id, [c.model_dump() for c in shortlist])
            emit(job_id, "done", f"Discovery complete — {len(shortlist)} issues ranked")

    except asyncio.TimeoutError:
        _fallback_discover(job_id, query, f"Job timed out after {JOB_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        logger.exception("discover job %s failed", job_id)
        _fallback_discover(job_id, query, str(exc))


def _fallback_discover(job_id: str, query: str | None, reason: str) -> None:
    from app.demo_results import load_discover_result
    saved = load_discover_result(query)
    if saved is not None:
        logger.warning("discover job %s using saved result for query %r: %s", job_id, query, reason)
        add_event(job_id, f"[fallback] Live run failed; serving saved result ({reason})")
        set_done_cached(job_id, saved["result"], saved["generated_at"])
    else:
        logger.warning("discover job %s failed, no saved result: %s", job_id, reason)
        set_failed(job_id, reason)


# ---------------------------------------------------------------------------
# Background task: investigate
# ---------------------------------------------------------------------------

async def run_investigate_job(
    job_id: str,
    issue_url: str,
    profile: Any,
    persona_id: str | None = None,
) -> None:
    from app.investigate import investigate_issue
    from app.report import build_report

    set_running(job_id)

    def _on_stage(stage_label: str) -> None:
        """Receive a fixed stage label from investigate_issue and emit it."""
        add_event(job_id, stage_label)

    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            bundle = await investigate_issue(issue_url, on_event=_on_stage)

            add_event(job_id, "Writing the report")
            report = await build_report(profile, bundle)

            set_done(job_id, report.model_dump())
            emit(job_id, "done", "Investigation complete")

    except asyncio.TimeoutError:
        _fallback_investigate(job_id, issue_url, persona_id, f"Job timed out after {JOB_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        logger.exception("investigate job %s failed", job_id)
        _fallback_investigate(job_id, issue_url, persona_id, str(exc))


def _fallback_investigate(job_id: str, issue_url: str, persona_id: str | None, reason: str) -> None:
    from app.demo_results import load_investigate_result
    saved = load_investigate_result(issue_url, persona_id)
    if saved is not None:
        logger.warning(
            "investigate job %s using saved result for %r / %r: %s",
            job_id, issue_url, persona_id, reason,
        )
        add_event(job_id, f"[fallback] Live run failed; serving saved result ({reason})")
        set_done_cached(job_id, saved["result"], saved["generated_at"])
    else:
        logger.warning("investigate job %s failed, no saved result: %s", job_id, reason)
        set_failed(job_id, reason)
