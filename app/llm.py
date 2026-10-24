"""
LLM helper for Nook.

Public interface:
    generate_json(prompt, schema, system=None) -> dict

Provider is chosen by LLM_PROVIDER env var (default: gemini).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import get_settings

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(text: str) -> str:
    m = _FENCE_RE.search(text)
    return m.group(1) if m else text.strip()


def _schema_dict(schema: dict | type[BaseModel]) -> dict:
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return schema.model_json_schema()
    return schema


def _validate(data: Any, schema: dict | type[BaseModel]) -> Any:
    """Validate data against schema. Returns data on success, raises ValueError on failure."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        schema.model_validate(data)  # raises ValidationError on failure
    else:
        # Basic structural check for plain dicts
        required = schema.get("required", [])
        if required and isinstance(data, dict):
            missing = [k for k in required if k not in data]
            if missing:
                raise ValueError(f"Missing required fields: {missing}")
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_json(
    prompt: str,
    schema: dict | type[BaseModel],
    system: str | None = None,
) -> Any:
    """
    Ask the configured LLM for a JSON response matching schema.

    Retries once on validation failure with the error message appended.
    Raises RuntimeError if both attempts fail.
    """
    settings = get_settings()

    if not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set")

    provider = settings.llm_provider.lower()
    schema_dict = _schema_dict(schema)

    async def attempt(extra: str = "") -> Any:
        full_prompt = prompt if not extra else f"{prompt}\n\nPrevious attempt failed: {extra}\nPlease fix and return valid JSON."
        if provider == "openai":
            raw = await _call_openai(full_prompt, schema_dict, system, settings)
        else:
            raw = await _call_gemini(full_prompt, schema_dict, system, settings)
        text = _strip_fences(raw)
        data = json.loads(text)
        return _validate(data, schema)

    try:
        return await attempt()
    except Exception as exc:
        logger.warning("generate_json first attempt failed (%s), retrying in 3s", exc)
        await asyncio.sleep(3)
        try:
            return await attempt(str(exc))
        except Exception as exc2:
            raise RuntimeError(f"generate_json failed after retry: {exc2}") from exc2


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

async def _call_gemini(
    prompt: str,
    schema: dict,
    system: str | None,
    settings,
) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.llm_model}:generateContent?key={settings.llm_api_key}"
    )
    contents = []
    if system:
        contents.append({"role": "user", "parts": [{"text": system}]})
        contents.append({"role": "model", "parts": [{"text": "Understood."}]})
    contents.append({"role": "user", "parts": [{"text": prompt}]})

    body = {
        "contents": contents,
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _gemini_schema(schema),
            "temperature": 0.1,
        },
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()

    return data["candidates"][0]["content"]["parts"][0]["text"]


def _gemini_schema(schema: dict) -> dict:
    """Strip keys Gemini doesn't support."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "title")}


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

async def _call_openai(
    prompt: str,
    schema: dict,
    system: str | None,
    settings,
) -> str:
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "strict": True, "schema": schema},
        },
    }
    async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]
