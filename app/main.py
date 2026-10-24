"""
Nook — FastAPI application entry point.
"""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.models import (
    DiscoverRequest,
    DiscoverResponse,
    InvestigateRequest,
    InvestigateResponse,
    JobKind,
    JobStatus,
    ProfileRequest,
    Profile,
)
import app.jobs as job_store
from app.mock_data import (
    DISCOVER_PROGRESS_STEPS,
    INVESTIGATE_PROGRESS_STEPS,
    MOCK_CANDIDATES,
    MOCK_INVESTIGATION,
    MOCK_PROFILE,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Nook API",
    description=(
        "Nook helps developers decide whether a specific open-source issue is right for them. "
        "It discovers candidate issues via TinyFish and investigates each one with a personalised report."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["meta"], summary="Health check")
def health():
    """Returns 200 OK when the server is up."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /profile
# ---------------------------------------------------------------------------

@app.post(
    "/profile",
    response_model=Profile,
    tags=["profile"],
    summary="Extract a structured developer profile from resume text",
)
async def build_profile(body: ProfileRequest) -> Profile:
    """
    Accepts raw resume text plus preferences and returns a structured Profile.

    In demo / mock mode the endpoint returns a realistic pre-built profile.
    In production an LLM will extract skills (tiered as experienced | familiar | inferred),
    interests, goals and environment from the resume text.
    """
    settings = get_settings()
    if settings.demo_mode or not settings.llm_api_key:
        return MOCK_PROFILE
    # TODO: wire up LLM extraction
    return MOCK_PROFILE


# ---------------------------------------------------------------------------
# POST /discover
# ---------------------------------------------------------------------------

@app.post(
    "/discover",
    response_model=DiscoverResponse,
    tags=["discovery"],
    summary="Start a discovery job — find candidate issues matching the profile",
)
async def discover(body: DiscoverRequest) -> DiscoverResponse:
    """
    Accepts a developer profile and an optional plain-English query.
    Returns a `job_id` immediately. Poll `GET /jobs/{id}` or stream
    `GET /jobs/{id}/stream` for progress and the final candidate list.
    """
    job = job_store.create_job(JobKind.discover)
    asyncio.create_task(_run_discover_mock(job.job_id))
    return DiscoverResponse(job_id=job.job_id)


async def _run_discover_mock(job_id: str) -> None:
    job_store.set_running(job_id)
    for step in DISCOVER_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
    result = [c.model_dump() for c in MOCK_CANDIDATES]
    job_store.set_done(job_id, result)


# ---------------------------------------------------------------------------
# POST /investigate
# ---------------------------------------------------------------------------

@app.post(
    "/investigate",
    response_model=InvestigateResponse,
    tags=["investigation"],
    summary="Start an investigation job for a single issue",
)
async def investigate(body: InvestigateRequest) -> InvestigateResponse:
    """
    Accepts an issue URL and a developer profile.
    Returns a `job_id` immediately. The agent reads the issue, linked PRs,
    commits and docs, then produces a personalised InvestigationReport.
    """
    job = job_store.create_job(JobKind.investigate)
    asyncio.create_task(_run_investigate_mock(job.job_id))
    return InvestigateResponse(job_id=job.job_id)


async def _run_investigate_mock(job_id: str) -> None:
    job_store.set_running(job_id)
    for step in INVESTIGATE_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
    job_store.set_done(job_id, MOCK_INVESTIGATION.model_dump())


# ---------------------------------------------------------------------------
# GET /jobs/{id}/stream  — SSE
# ---------------------------------------------------------------------------

@app.get(
    "/jobs/{job_id}/stream",
    tags=["jobs"],
    summary="Stream progress events for a job via Server-Sent Events",
    response_description="text/event-stream of JSON progress events, ending with a 'done' event",
)
async def stream_job(job_id: str):
    """
    Opens a Server-Sent Events stream for the given job.

    Each event is a JSON object: `{"type": "progress"|"done"|"error", "message": "..."}`.
    The stream closes after the `done` or `error` event.
    """
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    return StreamingResponse(
        _sse_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
        },
    )


async def _sse_generator(job_id: str):
    """Yield SSE-formatted events, polling the job store until done or failed."""
    seen = 0  # index of the last event we already emitted
    poll_interval = 0.4  # seconds between polls

    while True:
        job = job_store.get_job(job_id)
        if job is None:
            yield _sse_event({"type": "error", "message": "Job not found"})
            return

        # Emit any new events since we last checked
        new_events = job.events[seen:]
        for event in new_events:
            yield _sse_event({"type": "progress", "message": event.message, "timestamp": event.timestamp})
        seen += len(new_events)

        if job.status.value == "done":
            yield _sse_event({"type": "done", "message": "Job completed", "result": job.result})
            return

        if job.status.value == "failed":
            last_msg = job.events[-1].message if job.events else "Unknown error"
            yield _sse_event({"type": "error", "message": last_msg})
            return

        await asyncio.sleep(poll_interval)


def _sse_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------

@app.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    tags=["jobs"],
    summary="Get the current status and result of a job",
)
async def get_job(job_id: str) -> JobStatus:
    """
    Returns the full JobStatus for the given job_id, including all events
    and the final result once status is `done`.
    """
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job
