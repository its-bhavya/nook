"""
Nook — FastAPI application entry point.
"""
import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional

from app.config import get_settings
from app.profile import build_profile as _build_profile
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
    MOCK_PROFILE,
    MOCK_REPORTS,
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
    settings = get_settings()
    if settings.demo_mode or not settings.llm_api_key:
        return MOCK_PROFILE
    return await _build_profile(
        resume_text=body.resume_text,
        github_url=body.github_url,
        interests=body.interests,
        goals=body.goals,
        hours_per_week=body.hours_per_week,
        spoken_languages=body.spoken_languages,
        environment_override=body.environment,
    )


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
    Returns a job_id immediately. Poll GET /jobs/{id} or stream
    GET /jobs/{id}/stream for progress and the final candidate list.
    """
    settings = get_settings()
    job = job_store.create_job(JobKind.discover)

    if settings.demo_mode or not settings.tinyfish_api_key:
        asyncio.create_task(_run_discover_mock(job.job_id))
    else:
        asyncio.create_task(job_store.run_discover_job(job.job_id, body.profile, body.query))

    return DiscoverResponse(job_id=job.job_id)


async def _run_discover_mock(job_id: str) -> None:
    job_store.set_running(job_id)
    for step in DISCOVER_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
    result = [c.model_dump() for c in MOCK_CANDIDATES]
    job_store.set_done_mock(job_id, result)


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
    Returns a job_id immediately.
    """
    settings = get_settings()
    job = job_store.create_job(JobKind.investigate)

    if settings.demo_mode or not settings.tinyfish_api_key:
        asyncio.create_task(_run_investigate_mock(job.job_id, body.issue_url, body.persona_id))
    else:
        asyncio.create_task(
            job_store.run_investigate_job(job.job_id, body.issue_url, body.profile, body.persona_id)
        )

    return InvestigateResponse(job_id=job.job_id)


async def _run_investigate_mock(job_id: str, issue_url: str, persona_id: str | None) -> None:
    """
    Serve an in-process mock report for demo mode.
    Keyed by issue_url — fails with a clear error if the URL is not in MOCK_REPORTS.
    If persona_id is provided and a saved real-run file exists, that takes precedence
    (from_cache=True); otherwise the in-process mock is used (mock=True).
    """
    from app.demo_results import load_investigate_result

    # Prefer a saved real-run file when persona_id is supplied
    if persona_id:
        saved = load_investigate_result(issue_url, persona_id)
        if saved is not None:
            job_store.set_running(job_id)
            for step in INVESTIGATE_PROGRESS_STEPS:
                await asyncio.sleep(1)
                job_store.add_event(job_id, step)
            job_store.set_done_cached(job_id, saved["result"], saved["generated_at"])
            return

    report = MOCK_REPORTS.get(issue_url)
    if report is None:
        job_store.set_running(job_id)
        job_store.set_failed(
            job_id,
            f"No mock report available for {issue_url!r}. "
            "Run scripts/prerun.py to generate saved results, or use one of the known demo URLs.",
        )
        return

    job_store.set_running(job_id)
    for step in INVESTIGATE_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
    job_store.set_done_mock(job_id, report.model_dump())


# ---------------------------------------------------------------------------
# GET /demo/inputs
# ---------------------------------------------------------------------------

@app.get(
    "/demo/inputs",
    tags=["demo"],
    summary="Return personas, demo queries and demo issue URLs for the frontend demo flow",
)
def demo_inputs():
    """
    Returns static demo configuration read from data/.
    Personas include the full Profile so the frontend can pre-populate the profile form.
    """
    import json as _json
    from pathlib import Path

    data_dir = Path(__file__).parent.parent / "data"

    def _read(name: str):
        p = data_dir / name
        if not p.exists():
            return []
        return _json.loads(p.read_text(encoding="utf-8"))

    personas = _read("personas.json")
    demo_queries = _read("demo_queries.json")
    raw_urls = _read("demo_issue_urls.json")
    demo_issue_urls = [entry["url"] if isinstance(entry, dict) else entry for entry in raw_urls]

    return {
        "personas": personas,
        "demo_queries": demo_queries,
        "demo_issue_urls": demo_issue_urls,
    }


# ---------------------------------------------------------------------------
# POST /investigate/batch
# ---------------------------------------------------------------------------

from pydantic import BaseModel, Field
from typing import Optional


class BatchInvestigateRequest(BaseModel):
    issue_urls: list[str] = Field(..., description="List of GitHub issue URLs to investigate in parallel")
    profile: Profile = Field(..., description="Developer profile used to personalise each report")


class BatchInvestigateResponse(BaseModel):
    jobs: list[InvestigateResponse] = Field(..., description="One job per requested issue URL, in order")


@app.post(
    "/investigate/batch",
    response_model=BatchInvestigateResponse,
    tags=["investigation"],
    summary="Start parallel investigation jobs for multiple issues",
)
async def investigate_batch(body: BatchInvestigateRequest) -> BatchInvestigateResponse:
    """
    Accepts a list of issue URLs and a developer profile.
    Creates one investigation job per URL and returns all job IDs immediately.
    Parallel execution is rate-limited by the TinyFish concurrency semaphore.
    """
    settings = get_settings()
    responses: list[InvestigateResponse] = []

    for issue_url in body.issue_urls:
        job = job_store.create_job(JobKind.investigate)
        if settings.demo_mode or not settings.tinyfish_api_key:
            asyncio.create_task(_run_investigate_mock(job.job_id, issue_url, None))
        else:
            asyncio.create_task(
                job_store.run_investigate_job(job.job_id, issue_url, body.profile)
            )
        responses.append(InvestigateResponse(job_id=job.job_id))

    return BatchInvestigateResponse(jobs=responses)


# ---------------------------------------------------------------------------
# GET /jobs/{id}/stream  — SSE
# ---------------------------------------------------------------------------

@app.get(
    "/jobs/{job_id}/stream",
    tags=["jobs"],
    summary="Stream progress events for a job via Server-Sent Events",
)
async def stream_job(job_id: str):
    """
    SSE stream for the given job. Replays past events first, then streams
    new ones as they arrive. Sends a keepalive comment every 15 seconds.
    Closes with a done or failed event.
    """
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    return StreamingResponse(
        _sse_generator(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


_POLL_INTERVAL = 0.4   # seconds between polls
_KEEPALIVE_EVERY = 15  # seconds between SSE keepalive comments


async def _sse_generator(job_id: str):
    """
    Yield SSE-formatted strings.
    1. Replay all past events from the store.
    2. Poll for new events until the job reaches a terminal state.
    3. Send done / failed as the final event.
    4. Emit SSE comment keepalives to prevent proxy timeouts.
    """
    seen = 0
    last_keepalive = asyncio.get_event_loop().time()

    while True:
        job = job_store.get_job(job_id)
        if job is None:
            yield _sse_event({"type": "error", "message": "Job not found"})
            return

        # Emit any new events since we last checked
        new_events = job.events[seen:]
        for event in new_events:
            yield _sse_event({
                "type": "progress",
                "message": event.message,
                "timestamp": event.timestamp,
            })
        seen += len(new_events)

        now = asyncio.get_event_loop().time()
        if now - last_keepalive >= _KEEPALIVE_EVERY:
            yield ": keepalive\n\n"
            last_keepalive = now

        if job.status.value == "done":
            yield _sse_event({"type": "done", "message": "Job completed", "result": job.result})
            return

        if job.status.value == "failed":
            yield _sse_event({"type": "failed", "message": job.error or "Unknown error"})
            return

        await asyncio.sleep(_POLL_INTERVAL)


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
    job = job_store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job
