"""
TinyFish Agent API integration.

Single entry point: run_tinyfish(goal, url, ...)
Used by both discovery and investigation services.

Docs: https://tinyfish.mintlify.app/agent-api/reference
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base URL and paths
# ---------------------------------------------------------------------------

_BASE = "https://agent.tinyfish.ai/v1"
_SSE_ENDPOINT = f"{_BASE}/automation/run-sse"
_RUN_ENDPOINT = f"{_BASE}/automation/run"
_RUNS_ENDPOINT = f"{_BASE}/runs"

# ---------------------------------------------------------------------------
# Concurrency limiter — shared across all callers
# ---------------------------------------------------------------------------

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().tinyfish_max_concurrent)
    return _semaphore


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class TinyFishResult:
    ok: bool
    """True when the run completed and returned usable data."""

    raw_result: Any
    """Exact value of the `result` field from TinyFish, before any validation."""

    parsed_result: Any
    """Validated result matching output_schema, or None when validation failed / no schema."""

    error: str | None
    """Human-readable error message, or None on success."""

    elapsed_s: float
    """Wall-clock seconds from the start of our call to completion."""

    run_id: str | None = None
    """TinyFish run_id for debugging."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _headers() -> dict[str, str]:
    key = get_settings().tinyfish_api_key
    if not key:
        raise RuntimeError("TINYFISH_API_KEY is not set")
    return {"X-API-Key": key, "Content-Type": "application/json"}


def _build_body(
    goal: str,
    url: str,
    output_schema: dict | None,
    timeout_s: int,
) -> dict:
    body: dict[str, Any] = {
        "url": url,
        "goal": goal,
        "agent_config": {
            "max_duration_seconds": timeout_s,
        },
    }
    if output_schema is not None:
        body["output_schema"] = output_schema
    return body


def _extract_result(raw: Any, output_schema: dict | None) -> tuple[Any, str | None]:
    """
    Validate raw against output_schema (basic check only — just confirm top-level keys exist).
    Returns (parsed_result, error_message).
    """
    if raw is None:
        return None, "TinyFish returned null result"

    if output_schema is None:
        return raw, None

    required = output_schema.get("required", [])
    if isinstance(raw, dict) and all(k in raw for k in required):
        return raw, None

    # schema present but shape doesn't match — return raw anyway with a warning
    logger.warning(
        "TinyFish result shape mismatch. required=%s got keys=%s", required,
        list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__,
    )
    return raw, None  # still ok; caller can validate further


# ---------------------------------------------------------------------------
# SSE streaming call
# ---------------------------------------------------------------------------

async def _run_sse(
    goal: str,
    url: str,
    output_schema: dict | None,
    timeout_s: int,
    on_event: Callable[[str], Coroutine | None] | None,
) -> TinyFishResult:
    body = _build_body(goal, url, output_schema, timeout_s)
    t0 = time.monotonic()
    run_id: str | None = None

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s + 10)) as client:
            async with client.stream(
                "POST", _SSE_ENDPOINT,
                headers=_headers(),
                json=body,
            ) as response:
                if response.status_code != 200:
                    body_text = await response.aread()
                    elapsed = time.monotonic() - t0
                    err = f"HTTP {response.status_code}: {body_text.decode()[:300]}"
                    logger.error("TinyFish SSE failed: %s  elapsed=%.1fs", err, elapsed)
                    return TinyFishResult(
                        ok=False, raw_result=None, parsed_result=None,
                        error=err, elapsed_s=elapsed, run_id=run_id,
                    )

                buffer = ""
                async for chunk in response.aiter_text():
                    buffer += chunk
                    while "\n\n" in buffer:
                        block, buffer = buffer.split("\n\n", 1)
                        event = _parse_sse_block(block)
                        if event is None:
                            continue

                        etype = event.get("type", "")

                        if etype == "STARTED":
                            run_id = event.get("run_id")
                            logger.debug("TinyFish run started run_id=%s", run_id)

                        elif etype == "PROGRESS":
                            purpose = event.get("purpose", "")
                            logger.debug("TinyFish progress: %s", purpose)
                            if on_event and purpose:
                                result = on_event(purpose)
                                if asyncio.iscoroutine(result):
                                    await result

                        elif etype == "HEARTBEAT":
                            pass  # keep-alive, nothing to do

                        elif etype == "COMPLETE":
                            elapsed = time.monotonic() - t0
                            status = event.get("status", "")
                            raw = event.get("result")
                            err_obj = event.get("error")

                            if status != "COMPLETED" or err_obj:
                                err_msg = _format_error(err_obj) if err_obj else f"Run status: {status}"
                                logger.error(
                                    "TinyFish run failed run_id=%s err=%s elapsed=%.1fs",
                                    run_id, err_msg, elapsed,
                                )
                                return TinyFishResult(
                                    ok=False, raw_result=raw, parsed_result=None,
                                    error=err_msg, elapsed_s=elapsed, run_id=run_id,
                                )

                            parsed, parse_err = _extract_result(raw, output_schema)
                            if parse_err:
                                logger.warning("TinyFish result parse warning: %s", parse_err)

                            logger.info("TinyFish run complete run_id=%s elapsed=%.1fs", run_id, elapsed)
                            return TinyFishResult(
                                ok=True, raw_result=raw, parsed_result=parsed,
                                error=None, elapsed_s=elapsed, run_id=run_id,
                            )

        # Stream ended without a COMPLETE event
        elapsed = time.monotonic() - t0
        logger.error("TinyFish SSE stream ended without COMPLETE event. run_id=%s", run_id)
        return TinyFishResult(
            ok=False, raw_result=None, parsed_result=None,
            error="Stream closed without a COMPLETE event",
            elapsed_s=elapsed, run_id=run_id,
        )

    except httpx.TimeoutException:
        elapsed = time.monotonic() - t0
        logger.error("TinyFish SSE client timeout after %.1fs run_id=%s", elapsed, run_id)
        return TinyFishResult(
            ok=False, raw_result=None, parsed_result=None,
            error=f"Client-side timeout after {elapsed:.0f}s",
            elapsed_s=elapsed, run_id=run_id,
        )
    except asyncio.CancelledError:
        elapsed = time.monotonic() - t0
        logger.debug(
            "TinyFish SSE run cancelled (timeout or external cancel) run_id=%s elapsed=%.1fs",
            run_id, elapsed,
        )
        raise  # re-raise so the event loop can clean up properly


def _parse_sse_block(block: str) -> dict | None:
    """Parse a single SSE block (lines separated by \\n) into a dict."""
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[len("data:"):].strip()
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                logger.debug("Non-JSON SSE data line: %s", data[:100])
    return None


def _format_error(err_obj: Any) -> str:
    if isinstance(err_obj, dict):
        return err_obj.get("message") or err_obj.get("code") or str(err_obj)
    return str(err_obj)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_tinyfish(
    goal: str,
    url: str,
    output_schema: dict | None = None,
    timeout_s: int | None = None,
    on_event: Callable[[str], Coroutine | None] | None = None,
    max_attempts: int = 2,
) -> TinyFishResult:
    """
    Run a TinyFish automation and return a TinyFishResult.

    Args:
        goal:          Natural-language instruction for the agent.
        url:           Target page URL.
        output_schema: Optional JSON-schema object for structured output.
        timeout_s:     Wall-clock timeout in seconds for ONE attempt.
                       Defaults to TINYFISH_TIMEOUT from config.
        on_event:      Optional async or sync callable called with each PROGRESS message.
        max_attempts:  Total attempts (1 initial + retries). Timeouts are never retried.

    Returns:
        TinyFishResult with ok, raw_result, parsed_result, error, elapsed_s.
    """
    settings = get_settings()
    effective_timeout = timeout_s if timeout_s is not None else settings.tinyfish_timeout
    result: TinyFishResult | None = None

    for attempt in range(max_attempts):
        if attempt > 0:
            wait = 2 ** attempt
            logger.info("TinyFish retry attempt=%d waiting=%ds", attempt + 1, wait)
            await asyncio.sleep(wait)  # sleep OUTSIDE the semaphore so we don't block others

        t0 = time.monotonic()
        async with _get_semaphore():
            try:
                # Real wall-clock cap. httpx's timeout is per-read and SSE heartbeats
                # keep resetting it, so it never fires on a long-running stream.
                result = await asyncio.wait_for(
                    _run_sse(goal, url, output_schema, effective_timeout, on_event),
                    timeout=effective_timeout + 5,
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - t0
                logger.error("TinyFish hard wall-clock timeout after %.1fs url=%s", elapsed, url)
                result = TinyFishResult(
                    ok=False, raw_result=None, parsed_result=None,
                    error=f"Client-side timeout after {effective_timeout}s",
                    elapsed_s=elapsed,
                )

        if result.ok or not _should_retry_result(result):
            break

    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# TinyFish Fetch API  (free tier, ~2-8s, no agent steps consumed)
# ---------------------------------------------------------------------------

_FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"

@dataclass
class FetchPageResult:
    url: str
    text: str | None      # extracted markdown/text, None on failure
    error: str | None     # per-URL error code, None on success


async def fetch_pages(
    urls: list[str],
    *,
    intent: str | None = None,
    timeout_s: int = 30,
) -> list[FetchPageResult]:
    """
    Fetch one or more pages using the TinyFish Fetch API (free tier).

    Renders JS-heavy pages and returns clean markdown text.
    Up to 10 URLs per call; raises nothing — errors come back as FetchPageResult.error.

    Args:
        urls:      List of page URLs (max 10).
        intent:    Optional natural-language hint for better extraction quality.
        timeout_s: HTTP client timeout in seconds.

    Returns:
        One FetchPageResult per URL, in the same order as input.
    """
    if not urls:
        return []

    key = get_settings().tinyfish_api_key
    if not key:
        return [FetchPageResult(url=u, text=None, error="TINYFISH_API_KEY not set") for u in urls]

    body: dict = {"urls": urls, "format": "markdown"}
    if intent:
        body["intent"] = intent[:2000]

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s + 5)) as client:
            resp = await client.post(
                _FETCH_ENDPOINT,
                headers={"X-API-Key": key, "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code != 200:
                err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.error("TinyFish Fetch API error: %s", err)
                return [FetchPageResult(url=u, text=None, error=err) for u in urls]

            data = resp.json()

    except httpx.TimeoutException:
        return [FetchPageResult(url=u, text=None, error="fetch_timeout") for u in urls]
    except Exception as exc:  # noqa: BLE001
        logger.error("TinyFish Fetch API unexpected error: %s", exc)
        return [FetchPageResult(url=u, text=None, error=str(exc)) for u in urls]

    # Map results back to input order
    result_map: dict[str, FetchPageResult] = {}
    for item in data.get("results") or []:
        u = item.get("url") or ""
        result_map[u] = FetchPageResult(
            url=u,
            text=item.get("text") or item.get("content") or None,
            error=None,
        )
    for item in data.get("errors") or []:
        u = item.get("url") or ""
        result_map[u] = FetchPageResult(url=u, text=None, error=item.get("error") or "unknown")

    out: list[FetchPageResult] = []
    for u in urls:
        out.append(result_map.get(u) or FetchPageResult(url=u, text=None, error="missing_from_response"))
    return out


def _should_retry_result(result: TinyFishResult) -> bool:
    """
    Return True only for errors that are worth retrying.
    Conservative: retry only obvious transient server-side failures.
    Never retry timeouts (the run already used its full budget), agent
    failures (bad goal/url), client errors, or billing failures.
    """
    err = (result.error or "").lower()
    no_retry_phrases = (
        "agent_failure", "billing", "site_blocked", "invalid_input",
        "invalid_api_key", "missing_api_key", "task_failed",
        "timeout", "timed out",
    )
    if any(p in err for p in no_retry_phrases):
        return False
    # HTTP 4xx (except 429 rate limit) won't succeed on retry
    if err.startswith("http 4") and not err.startswith("http 429"):
        return False
    return True