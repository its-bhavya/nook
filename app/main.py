"""
Nook — FastAPI application entry point.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone  # noqa: F401  (kept for parity with jobs.py timestamps)

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Any, Optional

import app.ai_gateway as ai_gateway
import app.db as db
import app.storage as storage
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Best-effort Neon service init — never crash startup so the app stays debuggable."""
    try:
        await db.init_schema()
        logger.info("Neon Postgres schema initialized")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Neon Postgres schema init failed (non-fatal): %s", exc)
    yield
    await db.close_pool()


app = FastAPI(
    title="Nook API",
    description=(
        "Nook helps developers decide whether a specific open-source issue is right for them. "
        "It discovers candidate issues via TinyFish and investigates each one with a personalised report."
    ),
    version="0.1.0",
    lifespan=lifespan,
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


@app.get(
    "/health/services",
    tags=["meta"],
    summary="Status of each wired Neon service (Postgres, Object Storage, AI Gateway)",
)
async def health_services():
    """Report the live status of the three Neon services wired into the app."""
    settings = get_settings()
    report: dict = {}

    # Postgres
    if settings.database_url:
        ok = await db.ping()
        pg = {"configured": True, "ok": ok}
        if ok:
            try:
                pg["jobs_rows"] = await db.count_jobs()
            except Exception as exc:  # noqa: BLE001
                pg["jobs_rows_error"] = str(exc)
        report["postgres"] = pg
    else:
        report["postgres"] = {"configured": False, "ok": False}

    # Object Storage
    if storage.is_configured():
        report["object_storage"] = {
            "configured": True,
            "ok": storage.head_bucket(),
            "bucket": storage.bucket_name(),
        }
    else:
        report["object_storage"] = {"configured": False, "ok": False}

    # LLM (Neon AI Gateway when provisioned, else a direct LLM via LLM_API_KEY).
    # Used by POST /profile; status() never makes a paid call.
    report["llm"] = await ai_gateway.status()

    return report


# ---------------------------------------------------------------------------
# POST /parse-resume
# ---------------------------------------------------------------------------

@app.post(
    "/parse-resume",
    tags=["profile"],
    summary="Extract plain text from an uploaded PDF resume",
)
async def parse_resume(file: UploadFile = File(...)) -> dict:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    try:
        from pypdf import PdfReader
        import io
        data = await file.read()
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {e}")
    if not text:
        raise HTTPException(status_code=422, detail="No text could be extracted from the PDF.")
    return {"text": text}


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

    Real extraction runs through the Neon AI Gateway (when provisioned) or a
    direct LLM (LLM_API_KEY). The mock profile is only a fallback — used in
    demo mode, when no LLM is configured, the call fails, or the response
    won't validate.
    """
    settings = get_settings()

    if settings.demo_mode:
        return MOCK_PROFILE

    profile: Profile | None = None

    # 1) Neon AI Gateway
    if ai_gateway.is_configured():
        extracted = await ai_gateway.extract_profile(
            body.resume_text,
            {
                "interests": body.interests,
                "goals": body.goals,
                "hours_per_week": body.hours_per_week,
                "spoken_languages": body.spoken_languages,
                "environment": body.environment,
            },
        )
        if extracted:
            try:
                profile = Profile(**extracted)
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM returned an invalid profile shape; falling back: %s", exc)

    # 2) Direct LLM via LLM_API_KEY (also handles GitHub enrichment internally)
    if profile is None and settings.llm_api_key:
        try:
            profile = await _build_profile(
                resume_text=body.resume_text,
                github_url=body.github_url,
                interests=body.interests,
                goals=body.goals,
                hours_per_week=body.hours_per_week,
                spoken_languages=body.spoken_languages,
                environment_override=body.environment,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Direct LLM profile build failed; using mock: %s", exc)

    # 3) Fallback
    if profile is None:
        return MOCK_PROFILE

    # GitHub enrichment for the AI Gateway path (path 2 does it internally)
    if body.github_url and ai_gateway.is_configured():
        try:
            from app.github_profile import fetch_github_signals, merge_github_into_profile
            sig = await fetch_github_signals(body.github_url)
            if sig is not None:
                merge_github_into_profile(profile, sig)
        except Exception as exc:  # noqa: BLE001
            logger.warning("GitHub enrichment failed (non-fatal): %s", exc)

    return profile


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
    The job's lifecycle is mirrored to Neon Postgres.
    """
    settings = get_settings()
    job = job_store.create_job(JobKind.discover)
    await _persist(job.job_id)

    if settings.demo_mode or not settings.tinyfish_api_key:
        asyncio.create_task(_run_discover_mock(job.job_id))
    else:
        asyncio.create_task(_run_discover_live(job.job_id, body.profile, body.query))

    return DiscoverResponse(job_id=job.job_id)


async def _persist(job_id: str) -> None:
    """Mirror a job's in-memory state into Neon Postgres (best-effort)."""
    if not get_settings().database_url:
        return
    try:
        job = job_store.get_job(job_id)
        if job is not None:
            await db.save_job(job)
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist job %s failed (non-fatal): %s", job_id, exc)


async def _run_discover_live(job_id: str, profile: Any, query: str | None) -> None:
    """Run the real discover pipeline and persist state at start and finish."""
    await _persist(job_id)  # status: running (set inside run_discover_job)
    await job_store.run_discover_job(job_id, profile, query)
    await _persist(job_id)  # status: done or failed


async def _run_investigate_live(
    job_id: str, issue_url: str, profile: Any, persona_id: str | None
) -> None:
    """Run the real investigate pipeline and persist state at start and finish."""
    await _persist(job_id)  # status: running (set inside run_investigate_job)
    await job_store.run_investigate_job(job_id, issue_url, profile, persona_id)
    await _persist(job_id)  # status: done or failed


async def _run_discover_mock(job_id: str) -> None:
    job_store.set_running(job_id)
    await _persist(job_id)
    for step in DISCOVER_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
        await _persist(job_id)
    result = [c.model_dump() for c in MOCK_CANDIDATES]
    job_store.set_done_mock(job_id, result)
    await _persist(job_id)


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
    Returns a job_id immediately. The agent reads the issue, linked PRs,
    commits and docs, then produces a personalised InvestigationReport that is
    also persisted to Neon Object Storage.
    """
    settings = get_settings()
    job = job_store.create_job(JobKind.investigate)
    await _persist(job.job_id)

    if settings.demo_mode or not settings.tinyfish_api_key:
        asyncio.create_task(_run_investigate_mock(job.job_id, body.issue_url, body.persona_id))
    else:
        asyncio.create_task(
            _run_investigate_live(job.job_id, body.issue_url, body.profile, body.persona_id)
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
            await _persist(job_id)
            for step in INVESTIGATE_PROGRESS_STEPS:
                await asyncio.sleep(1)
                job_store.add_event(job_id, step)
                await _persist(job_id)
            job_store.set_done_cached(job_id, saved["result"], saved["generated_at"])
            await _persist(job_id)
            return

    report = MOCK_REPORTS.get(issue_url)
    if report is None:
        job_store.set_running(job_id)
        job_store.set_failed(
            job_id,
            f"No mock report available for {issue_url!r}. "
            "Run scripts/prerun.py to generate saved results, or use one of the known demo URLs.",
        )
        await _persist(job_id)
        return

    job_store.set_running(job_id)
    await _persist(job_id)
    for step in INVESTIGATE_PROGRESS_STEPS:
        await asyncio.sleep(1)
        job_store.add_event(job_id, step)
        await _persist(job_id)

    result = report.model_dump(mode="json")

    # Persist the investigation report to Neon Object Storage.
    if storage.is_configured():
        try:
            key = await asyncio.to_thread(
                storage.put_json, f"reports/{job_id}.json", result
            )
            result = {**result, "storage_key": key}
        except Exception as exc:  # noqa: BLE001
            logger.warning("storage upload failed (non-fatal): %s", exc)
            result = {**result, "storage_key": None, "storage_error": str(exc)}

    job_store.set_done_mock(job_id, result)
    await _persist(job_id)


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
        await _persist(job.job_id)
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
    """
    Returns the full JobStatus for the given job_id, including all events
    and the final result once status is `done`. Falls back to Neon Postgres
    when the job is no longer in the in-memory store (e.g. after a restart).
    """
    job = job_store.get_job(job_id)
    if job is None and get_settings().database_url:
        persisted = await db.load_job(job_id)
        if persisted is not None:
            return persisted
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
    return job