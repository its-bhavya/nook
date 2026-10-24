"""
In-memory job store and background task runners.

Store: dict[job_id -> JobStatus]
emit(job_id, stage, message) appends a staged event.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models import JobEvent, JobKind, JobStatus, JobStatusEnum

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

_store: dict[str, JobStatus] = {}

JOB_TIMEOUT = 300  # seconds; individual investigate tasks share this budget


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

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
    """Append a progress event with an explicit stage label."""
    job = _store.get(job_id)
    if job is None:
        return
    job.events.append(JobEvent(
        message=f"[{stage}] {message}",
        timestamp=_now_iso(),
    ))


def add_event(job_id: str, message: str) -> None:
    """Compat shim — append a plain event."""
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


# ---------------------------------------------------------------------------
# Report builder (stub for prompt 7)
# ---------------------------------------------------------------------------

def _build_report_from_bundle(bundle: Any, issue_url: str, profile: Any) -> dict:
    """
    Stub report generator. Wraps the EvidenceBundle in an InvestigationReport
    shape without an extra LLM call.  Replace with the real prompt-7 call later.
    """
    from app.models import (
        InvestigationReport, FitAssessment, FitLevel,
        SourceClaim, Blocker, ActivitySignal, RelatedPR, PRState,
    )

    # Derive fit from already_resolved flag and partial flag
    if bundle.already_resolved:
        fit_level = FitLevel.weak
        fit_reasoning = "This issue appears to be already resolved or closed."
    elif bundle.partial:
        fit_level = FitLevel.weak
        fit_reasoning = "Investigation was incomplete; cannot assess fit reliably."
    else:
        fit_level = FitLevel.possible
        fit_reasoning = "No blockers detected during preliminary investigation."

    sources_say = [
        SourceClaim(claim=f.claim, source_url=f.source_url or issue_url)
        for f in bundle.findings
        if f.source_url
    ]

    prerequisites = [
        f.claim for f in bundle.findings if f.kind == "prerequisite"
    ]

    blockers = [
        Blocker(text=f.claim, source_url=f.source_url)
        for f in bundle.findings if f.kind == "blocker"
    ]

    activity_signals = [
        ActivitySignal(signal=sig.signal, source_url=sig.source_url or issue_url)
        for sig in bundle.activity_signals
    ]

    related_prs = []
    for pr in bundle.related_prs:
        try:
            state = PRState(pr.state)
        except ValueError:
            state = PRState.open
        related_prs.append(RelatedPR(url=pr.url, state=state))

    report = InvestigationReport(
        issue_url=issue_url,
        fit=FitAssessment(level=fit_level, reasoning=fit_reasoning),
        sources_say=sources_say,
        interpretation=[],
        prerequisites=prerequisites,
        blockers=blockers,
        activity_signals=activity_signals,
        related_prs=related_prs,
        first_steps=[],
        unknowns=bundle.unknowns,
    )
    return report.model_dump()


# ---------------------------------------------------------------------------
# Background task: discover
# ---------------------------------------------------------------------------

async def run_discover_job(
    job_id: str,
    profile: Any,
    query: str | None,
) -> None:
    """Run discover_issues -> rank_candidates, store shortlist as job result."""
    from app.discover import discover_issues
    from app.rank import rank_candidates
    from app.config import get_settings

    set_running(job_id)

    def _on_event(msg: str) -> None:
        emit(job_id, "discover", msg)

    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            emit(job_id, "discover", "Starting issue discovery")
            candidates = await discover_issues(profile, query, on_event=_on_event)

            emit(job_id, "rank", f"Ranking {len(candidates)} candidates")
            shortlist = await rank_candidates(profile, candidates)

            set_done(job_id, shortlist)
            emit(job_id, "done", f"Discovery complete — {len(shortlist)} issues ranked")

    except asyncio.TimeoutError:
        set_failed(job_id, f"Job timed out after {JOB_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        logger.exception("discover job %s failed", job_id)
        set_failed(job_id, str(exc))


# ---------------------------------------------------------------------------
# Background task: investigate (single issue)
# ---------------------------------------------------------------------------

async def run_investigate_job(
    job_id: str,
    issue_url: str,
    profile: Any,
) -> None:
    """Run investigate_issue -> build report, store as job result."""
    from app.investigate import investigate_issue

    set_running(job_id)

    def _on_event(msg: str) -> None:
        emit(job_id, "investigate", msg)

    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            emit(job_id, "investigate", f"Investigating {issue_url}")
            bundle = await investigate_issue(issue_url, on_event=_on_event)

            emit(job_id, "report", "Building report")
            report = _build_report_from_bundle(bundle, issue_url, profile)

            set_done(job_id, report)
            emit(job_id, "done", "Investigation complete")

    except asyncio.TimeoutError:
        set_failed(job_id, f"Job timed out after {JOB_TIMEOUT}s")
    except Exception as exc:  # noqa: BLE001
        logger.exception("investigate job %s failed", job_id)
        set_failed(job_id, str(exc))
