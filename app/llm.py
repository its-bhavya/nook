"""
LLM helper for Nook.

Public interface:
    generate_json(prompt: str, schema: dict) -> dict | list | None

Supports Gemini (default) and OpenAI via LLM_PROVIDER env var.
Returns None on failure — callers must handle gracefully.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def generate_json(prompt: str, schema: dict) -> Any:
    """
    Ask the configured LLM to return a JSON object matching *schema*.

    Args:
        prompt: Natural-language instruction that includes all context the LLM needs.
        schema: JSON-schema object describing the expected shape of the response.

    Returns:
        Parsed Python object (dict or list), or None on any failure.
    """
    settings = get_settings()

    if not settings.llm_api_key:
        logger.warning("generate_json: LLM_API_KEY not set — returning None")
        return None

    provider = settings.llm_provider.lower()

    try:
        if provider == "openai":
            return await _call_openai(prompt, schema, settings)
        else:
            return await _call_gemini(prompt, schema, settings)
    except Exception as exc:  # noqa: BLE001
        logger.error("generate_json failed provider=%s: %s", provider, exc)
        return None


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------

async def _call_gemini(prompt: str, schema: dict, settings) -> Any:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.llm_model}:generateContent?key={settings.llm_api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
        },
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _gemini_schema(schema: dict) -> dict:
    """
    Gemini's responseSchema uses a subset of JSON-schema.
    Strip keys it doesn't support ($schema, description at top level, etc.)
    and pass through as-is otherwise — it's permissive enough for our needs.
    """
    return {k: v for k, v in schema.items() if k not in ("$schema", "title")}


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

async def _call_openai(prompt: str, schema: dict, settings) -> Any:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    body = {
        "model": settings.llm_model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": schema},
        },
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    text = data["choices"][0]["message"]["content"]
    return json.loads(text)
