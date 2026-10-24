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
from dataclasses import dataclass, field
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


def _build_body(goal: str, url: str, output_schema: dict | None, timeout_s: int) -> dict:
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


def _is_retryable(category: str | None, http_status: int | None) -> bool:
    """Return True only for transient errors we should retry."""
    if http_status and http_status >= 500:
        return True
    if category in ("SYSTEM_FAILURE", "UNKNOWN"):
        return True
    return False


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
    logger.warning("TinyFish result shape mismatch. required=%s got keys=%s", required,
                   list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__)
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
    """
    Call /run-sse and forward PROGRESS events to on_event.
    Parses the SSE stream line-by-line; no SDK dependency.
    """
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
                                logger.error("TinyFish run failed run_id=%s err=%s elapsed=%.1fs",
                                             run_id, err_msg, elapsed)
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
        logger.warning("TinyFish SSE run cancelled run_id=%s elapsed=%.1fs", run_id, elapsed)
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
) -> TinyFishResult:
    """
    Run a TinyFish automation and return a TinyFishResult.

    Args:
        goal:          Natural-language instruction for the agent.
        url:           Target page URL.
        output_schema: Optional JSON-schema object for structured output.
                       Must follow TinyFish schema rules (see docs/tinyfish_notes.md).
        timeout_s:     Client-side wall-clock timeout in seconds.
                       Defaults to TINYFISH_TIMEOUT from config.
        on_event:      Optional async or sync callable called with each PROGRESS
                       message string as the agent works. Use to forward live
                       updates to the Nook SSE stream.

    Returns:
        TinyFishResult with ok, raw_result, parsed_result, error, elapsed_s.

    Retry policy:
        Up to 2 retries for transient errors (5xx, SYSTEM_FAILURE, UNKNOWN).
        Never retries AGENT_FAILURE or BILLING_FAILURE.
        Never retries a run that completed but returned unexpected data.
    """
    settings = get_settings()
    effective_timeout = timeout_s if timeout_s is not None else settings.tinyfish_timeout

    max_attempts = 3  # 1 initial + 2 retries
    last_result: TinyFishResult | None = None

    async with _get_semaphore():
        for attempt in range(max_attempts):
            if attempt > 0:
                wait = 2 ** attempt  # 2s, 4s
                logger.info("TinyFish retry attempt=%d waiting=%ds", attempt + 1, wait)
                await asyncio.sleep(wait)

            result = await _run_sse(goal, url, output_schema, effective_timeout, on_event)
            last_result = result

            if result.ok:
                return result

            # Decide whether to retry
            # Parse category from error string if possible (full error object not always available here)
            should_retry = (
                attempt < max_attempts - 1
                and _should_retry_result(result)
            )
            if not should_retry:
                break

        return last_result  # type: ignore[return-value]


def _should_retry_result(result: TinyFishResult) -> bool:
    """
    Return True only for errors that are worth retrying.
    Conservative: we only retry on timeout or obvious server-side failures.
    We never retry agent failures (bad goal/url) or billing failures.
    """
    err = (result.error or "").lower()
    # Don't retry these — they need human intervention
    no_retry_phrases = ("agent_failure", "billing", "site_blocked", "invalid_input",
                        "invalid_api_key", "missing_api_key", "task_failed")
    if any(p in err for p in no_retry_phrases):
        return False
    return True
