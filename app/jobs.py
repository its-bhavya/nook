"""
In-memory job store. No database, no Redis — just a dict.
Jobs are keyed by job_id (UUID string).
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models import JobEvent, JobKind, JobStatus, JobStatusEnum


# Module-level store — lives for the lifetime of the process.
_store: dict[str, JobStatus] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(kind: JobKind) -> JobStatus:
    """Create a new queued job and register it in the store."""
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


def add_event(job_id: str, message: str) -> None:
    """Append a progress event to the job."""
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


def set_failed(job_id: str, error: str) -> None:
    job = _store.get(job_id)
    if job:
        job.status = JobStatusEnum.failed
        job.events.append(JobEvent(message=f"ERROR: {error}", timestamp=_now_iso()))
