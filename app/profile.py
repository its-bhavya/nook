"""
Profile builder for Nook.

build_profile(...) -> Profile

Extracts skills into three tiers:
  - experienced: used in a real project or job with stated outcomes
  - familiar: coursework, tutorials, or brief exposure
  - inferred: implied by adjacent skills or context but not explicitly shown

Personal contact details (phone, email) are stripped before storage.
GitHub URL is stored as a plain string — no API calls are made.
"""
from __future__ import annotations

import re

from app.llm import generate_json
from app.models import Environment, EnvironmentOverride, Profile, Skill, SkillLevel

# ---------------------------------------------------------------------------
# PII scrubbers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(
    r"""
    (?:\+?\d{1,3}[\s\-.])?     # optional country code
    (?:\(?\d{1,4}\)?[\s\-.])?  # optional area code
    \d{2,4}[\s\-.]             # first group
    \d{2,4}[\s\-.]             # second group
    \d{2,6}                    # last group
    """,
    re.VERBOSE,
)


def _strip_pii(text: str) -> str:
    text = _EMAIL_RE.sub("[email]", text)
    text = _PHONE_RE.sub("[phone]", text)
    return text


# ---------------------------------------------------------------------------
# JSON schema for LLM response
# ---------------------------------------------------------------------------

_SKILL_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "level": {"type": "string", "enum": ["experienced", "familiar", "inferred"]},
                    "evidence": {"type": "string"},
                },
                "required": ["name", "level", "evidence"],
            },
        }
    },
    "required": ["skills"],
}

_SYSTEM_PROMPT = (
    "You are a precise skill extractor. You read resume or bio text and identify technical skills. "
    "You return JSON only — no prose, no markdown fences."
)


def _build_extraction_prompt(resume_text: str) -> str:
    return f"""Extract all technical skills from the resume below.

Classify each skill as exactly one of:
- "experienced": the person used this technology in a job or project AND the resume describes a concrete outcome or responsibility (e.g. "built", "deployed", "led", "reduced", "increased").
- "familiar": mentioned in coursework, a personal project without outcomes, or described with hedging language like "exposure to" or "learning".
- "inferred": not explicitly named but strongly implied by adjacent skills or job context. Use this sparingly and only when confident.

IMPORTANT: A technology that appears only in a list of skills with no supporting context must be classified "familiar" at best — never "experienced".

For each skill include a short evidence string (≤ 20 words) taken directly from the resume text.

RESUME:
{resume_text}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def build_profile(
    resume_text: str,
    github_url: str | None,
    interests: list[str],
    goals: str,
    hours_per_week: int,
    spoken_languages: list[str],
    environment_override: EnvironmentOverride | None = None,
) -> Profile:
    """
    Build a structured Profile from resume text and user-supplied preferences.

    - Strips PII (email, phone) before processing.
    - Does not call GitHub APIs; github_url is stored as-is.
    - Classifies skills into experienced / familiar / inferred tiers.
    - environment_override fields win over anything the LLM might have guessed.
    """
    clean_resume = _strip_pii(resume_text)
    prompt = _build_extraction_prompt(clean_resume)

    result = await generate_json(prompt, _SKILL_EXTRACTION_SCHEMA, system=_SYSTEM_PROMPT)

    skills = [
        Skill(
            name=s["name"],
            level=SkillLevel(s["level"]),
            evidence=s["evidence"],
        )
        for s in result.get("skills", [])
    ]

    environment = Environment(
        has_llm_api_keys=[],
        os=None,
    )

    # User-supplied values override whatever the LLM produced
    if environment_override is not None:
        if environment_override.has_llm_api_keys is not None:
            environment.has_llm_api_keys = environment_override.has_llm_api_keys
        if environment_override.os is not None:
            environment.os = environment_override.os

    return Profile(
        skills=skills,
        interests=interests,
        goals=goals,
        hours_per_week=hours_per_week,
        spoken_languages=spoken_languages,
        environment=environment,
    )
