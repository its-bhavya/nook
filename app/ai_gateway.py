"""
LLM integration for profile extraction.

Two OpenAI-compatible backends, in priority order:

1. Neon AI Gateway — when NEON_AI_GATEWAY_* are set (requires a paid Neon plan
   + prepaid credits; not available on the Free plan).
2. A direct LLM provider — using LLM_API_KEY (+ optional LLM_BASE_URL, LLM_MODEL).
   Provider defaults: `openai` -> api.openai.com, `gemini` -> Google's
   OpenAI-compatible endpoint. Any OpenAI-compatible base URL works via
   LLM_BASE_URL.

The chat-completions call is identical for both — only the base URL, key and
default model change. Everything is wrapped so a missing/invalid key or a
malformed response falls back to the mock profile instead of crashing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _default_model(provider: str) -> str:
    return {
        "openai": "gpt-4o-mini",
        "gemini": "gemini-2.0-flash",
    }.get(provider, "")


def is_neon_gateway_configured() -> bool:
    s = get_settings()
    return bool(s.neon_ai_gateway_token and s.neon_ai_gateway_base_url)


def is_direct_llm_configured() -> bool:
    return bool(get_settings().llm_api_key)


def is_configured() -> bool:
    """True when either the Neon AI Gateway or a direct LLM key is available."""
    return is_neon_gateway_configured() or is_direct_llm_configured()


def _resolve() -> dict:
    """
    Pick the active backend and return {provider, base, path_prefix, key, model}.

    `path_prefix` is appended before /chat/completions: the Neon gateway base is
    a bare host (needs "/v1"); direct-provider base URLs already include the
    version path (e.g. ".../v1", ".../v1beta/openai").
    """
    s = get_settings()
    if is_neon_gateway_configured():
        return {
            "provider": "neon-gateway",
            "base": s.neon_ai_gateway_base_url.rstrip("/"),
            "path_prefix": "/v1",
            "key": s.neon_ai_gateway_token,
            "model": s.ai_gateway_model,
        }

    provider = (s.llm_provider or "openai").lower()
    if s.llm_base_url:
        base = s.llm_base_url.rstrip("/")
    elif provider == "openai":
        base = "https://api.openai.com/v1"
    elif provider == "gemini":
        base = "https://generativelanguage.googleapis.com/v1beta/openai"
    else:
        base = s.llm_base_url.rstrip("/")  # empty -> chat_complete will raise

    return {
        "provider": f"direct:{provider}",
        "base": base,
        "path_prefix": "",
        "key": s.llm_api_key,
        "model": s.llm_model or _default_model(provider),
    }


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Health / discovery (Neon gateway only)
# ---------------------------------------------------------------------------

async def list_models(timeout: float = 20.0) -> dict[str, Any]:
    """GET /v1/models from the Neon AI Gateway (not served by direct providers)."""
    if not is_neon_gateway_configured():
        return {"configured": False, "models": []}
    s = get_settings()
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(
            f"{s.neon_ai_gateway_base_url.rstrip('/')}/v1/models",
            headers=_headers(s.neon_ai_gateway_token),
        )
        resp.raise_for_status()
        data = resp.json()
    models = [m.get("id") for m in data.get("data", []) if m.get("enabled", True)]
    return {"configured": True, "models": models}


async def status() -> dict:
    """Status reported by /health/services — never makes a paid call."""
    if is_neon_gateway_configured():
        try:
            models = await list_models()
            return {
                "configured": True,
                "provider": "neon-gateway",
                "ok": True,
                "models_sample": models.get("models", [])[:10],
            }
        except Exception as exc:  # noqa: BLE001
            return {"configured": True, "provider": "neon-gateway", "ok": False, "error": str(exc)}
    if is_direct_llm_configured():
        r = _resolve()
        return {
            "configured": True,
            "provider": r["provider"],
            "ok": None,
            "model": r["model"],
            "note": "direct LLM configured (not pinged to avoid cost)",
        }
    return {
        "configured": False,
        "ok": False,
        "note": "no LLM configured (NEON_AI_GATEWAY_* and LLM_API_KEY unset) — /profile returns mock",
    }


# ---------------------------------------------------------------------------
# Chat completions (works for both backends)
# ---------------------------------------------------------------------------

async def chat_complete(
    messages: list[dict[str, str]],
    model: Optional[str] = None,
    temperature: float = 0.2,
    timeout: float = 60.0,
) -> str:
    """Call the active backend's OpenAI-compatible chat completions and return text."""
    if not is_configured():
        raise RuntimeError("No LLM configured (NEON_AI_GATEWAY_* or LLM_API_KEY unset)")
    r = _resolve()
    if not r["base"] or not r["key"]:
        raise RuntimeError(f"LLM backend '{r['provider']}' missing base URL or key")
    url = f"{r['base']}{r['path_prefix']}/chat/completions"
    body = {"model": model or r["model"], "messages": messages, "temperature": temperature}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=_headers(r["key"]), json=body)
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    return choices[0]["message"]["content"] if choices else ""


# ---------------------------------------------------------------------------
# Profile extraction (used by POST /profile)
# ---------------------------------------------------------------------------

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

_PROFILE_SCHEMA = (
    "{\n"
    '  "skills": [{"name": str, "level": "experienced|familiar|inferred", "evidence": str}],\n'
    '  "interests": [str],\n'
    '  "goals": str,\n'
    '  "hours_per_week": int,\n'
    '  "spoken_languages": [str],\n'
    '  "environment": {"has_llm_api_keys": [str], "os": str|null}\n'
    "}"
)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model response (tolerates prose/fences)."""
    # Strip ```json ... ``` fences if present.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    match = _JSON_BLOCK.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def extract_profile(resume_text: str, preferences: dict[str, Any]) -> Optional[dict]:
    """
    Ask the LLM to turn resume text + preferences into the Nook Profile JSON
    shape (see app.models.Profile). Returns None on any failure so the caller
    can fall back to the mock profile — never raises.
    """
    if not is_configured():
        return None
    system = (
        "You extract a structured developer profile from resume text. "
        "Respond with ONLY a JSON object (no prose, no markdown fences) matching exactly:\n"
        + _PROFILE_SCHEMA
        + "\nTier skills as 'experienced' only when the resume shows real use, "
        "'familiar' for some exposure, 'inferred' for mere mentions. "
        "Use the preferences to fill interests/goals/hours/spoken_languages when given."
    )
    user_msg = (
        f"Resume:\n{resume_text}\n\n"
        f"Preferences: {json.dumps(preferences, default=str)}"
    )
    try:
        text = await chat_complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        return _extract_json(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM profile extraction failed: %s", exc)
        return None
