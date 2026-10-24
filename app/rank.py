"""
Candidate ranker for Nook.

rank_candidates(profile, candidates, top_n=5) -> list[dict]

One LLM call. Returns an ordered shortlist of up to top_n issues, each with:
  - issue: the issue URL
  - fit_hint: "strong" | "possible" | "weak"
  - reasons: one-line explanation grounded in the candidate fields
"""
from __future__ import annotations

import json

from app.llm import generate_json
from app.models import CandidateIssue, Profile

_RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "fit_hint": {"type": "string", "enum": ["strong", "possible", "weak"]},
                    "reasons": {"type": "string"},
                },
                "required": ["issue", "fit_hint", "reasons"],
            },
        }
    },
    "required": ["ranked"],
}

_SYSTEM_PROMPT = (
    "You are a precise fit ranker. Compare a developer profile against open-source issues "
    "and rank them by fit. Base every reason strictly on the fields provided — do not invent "
    "facts. If information is absent, say so in the reasons field. Return JSON only."
)


def _build_ranking_prompt(profile: Profile, candidates: list[CandidateIssue], top_n: int) -> str:
    profile_summary = {
        "skills": [
            {"name": s.name, "level": s.level.value} for s in profile.skills
        ],
        "interests": profile.interests,
        "goals": profile.goals,
        "hours_per_week": profile.hours_per_week,
        "spoken_languages": profile.spoken_languages,
    }

    candidate_list = [
        {
            "url": c.url,
            "repo": c.repo,
            "title": c.title,
            "labels": c.labels,
        }
        for c in candidates
    ]

    return f"""Rank the following open-source issues by fit for this developer. Return the top {top_n}.

DEVELOPER PROFILE:
{json.dumps(profile_summary, indent=2)}

CANDIDATE ISSUES:
{json.dumps(candidate_list, indent=2)}

Rules:
- Base fit_hint and reasons only on the fields above.
- If a field is empty or missing, say "no information available" in reasons rather than guessing.
- reasons must be a single line (≤ 25 words).
- Order from best fit to worst fit."""


async def rank_candidates(
    profile: Profile,
    candidates: list[CandidateIssue],
    top_n: int = 5,
) -> list[dict]:
    """
    Rank candidates against the profile and return an ordered shortlist.

    Returns a list of dicts with keys: issue, fit_hint, reasons.
    """
    if not candidates:
        return []

    prompt = _build_ranking_prompt(profile, candidates, top_n)
    result = await generate_json(prompt, _RANKING_SCHEMA, system=_SYSTEM_PROMPT)
    return result.get("ranked", [])[:top_n]
