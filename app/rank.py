"""
Candidate ranker for Nook.

rank_candidates(profile, candidates, top_n=5) -> list[CandidateIssue]

Pipeline:
  1. LLM scores fit and writes one-sentence reason per candidate.
  2. Code enforces diversity: at most 1 per repo, at most 2 per theme.
  3. Code assembles final list of top_n covering ≥ 3 themes when possible.
  4. Reason validation: replace any weak/invalid reason with a deterministic fallback.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from app.discover import _fallback_reason, validate_reason
from app.llm import generate_json
from app.models import CandidateIssue, Profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM schema and prompts
# ---------------------------------------------------------------------------

_RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "scored": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue":     {"type": "string"},
                    "fit_hint":  {"type": "string", "enum": ["strong", "possible", "weak"]},
                    "reason":    {"type": "string"},
                },
                "required": ["issue", "fit_hint", "reason"],
            },
        }
    },
    "required": ["scored"],
}

_SYSTEM_PROMPT = (
    "You are a precise open-source issue fit scorer. "
    "Score each candidate strictly from the structured data given — "
    "do not invent facts, do not use the phrase 'good first issue' or 'beginner', "
    "and do not describe issue content that was not provided. "
    "Return JSON only."
)


def _build_ranking_prompt(profile: Profile, candidates: list[CandidateIssue]) -> str:
    profile_summary = {
        "skills": [{"name": s.name, "level": s.level.value} for s in profile.skills],
        "interests": profile.interests,
        "goals": profile.goals,
        "hours_per_week": profile.hours_per_week,
    }

    candidate_list = [
        {
            "url": c.url,
            "repo": c.repo,
            "title": c.title,
            "labels": c.labels,
            "opened": c.opened,
            "comments": c.comments,
            "theme": c.theme,
            "matched_skills": c.matched_skills,
        }
        for c in candidates
    ]

    return f"""Score each open-source issue for fit against this developer profile.

DEVELOPER PROFILE:
{json.dumps(profile_summary, indent=2)}

CANDIDATE ISSUES:
{json.dumps(candidate_list, indent=2)}

For every candidate produce:
- issue: the URL (copy exactly)
- fit_hint: "strong" | "possible" | "weak"
- reason: one sentence (≤ 30 words) that:
    * mentions at least one matched_skill or theme AND one fact (comment count, age, label)
    * does NOT say "good first issue" or "beginner"
    * does NOT claim a discussion if comments is 0
    * does NOT describe content that was not provided

Return all {len(candidates)} candidates scored."""


# ---------------------------------------------------------------------------
# Diversity enforcement (pure code)
# ---------------------------------------------------------------------------

def _enforce_diversity(
    scored: list[tuple[CandidateIssue, str, str]],
    top_n: int,
) -> list[CandidateIssue]:
    """
    scored: list of (candidate, fit_hint, reason) ordered best-first by LLM.
    Rules:
    - At most 1 issue per repo.
    - At most 2 issues per theme.
    - Final list covers ≥ 3 distinct themes when enough candidates allow it.
    - Fill remaining slots by score order.
    Returns up to top_n CandidateIssue objects with reason set.
    """
    repo_seen: set[str] = set()
    theme_count: dict[str, int] = defaultdict(int)
    selected: list[CandidateIssue] = []

    for c, fit_hint, reason in scored:
        if len(selected) >= top_n:
            break
        if c.repo in repo_seen:
            continue
        theme = c.theme or "generic"
        if theme_count[theme] >= 2:
            continue
        # Validate / replace reason
        if not validate_reason(reason, c):
            logger.debug("Replacing invalid reason for %s", c.url)
            reason = _fallback_reason(c)
        c.reason = reason
        repo_seen.add(c.repo)
        theme_count[theme] += 1
        selected.append(c)

    # Theme coverage check — if we have spare slots and uncovered themes, try to fill
    if len(selected) < top_n:
        covered_themes = {c.theme or "generic" for c in selected}
        for c, fit_hint, reason in scored:
            if len(selected) >= top_n:
                break
            if c in selected:
                continue
            if c.repo in repo_seen:
                continue
            theme = c.theme or "generic"
            if theme in covered_themes:
                continue  # only add if it brings a new theme
            if not validate_reason(reason, c):
                reason = _fallback_reason(c)
            c.reason = reason
            repo_seen.add(c.repo)
            theme_count[theme] += 1
            covered_themes.add(theme)
            selected.append(c)

    return selected[:top_n]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def rank_candidates(
    profile: Profile,
    candidates: list[CandidateIssue],
    top_n: int = 5,
) -> list[CandidateIssue]:
    """
    Rank candidates against the profile.

    Returns up to top_n CandidateIssue objects with reason set.
    """
    if not candidates:
        return []

    prompt = _build_ranking_prompt(profile, candidates)
    result = await generate_json(prompt, _RANKING_SCHEMA, system=_SYSTEM_PROMPT)
    scored_raw: list[dict] = result.get("scored", []) if result else []

    # Build a URL → candidate lookup
    by_url: dict[str, CandidateIssue] = {c.url: c for c in candidates}

    # Assemble (candidate, fit_hint, reason) keeping LLM order (best first)
    scored: list[tuple[CandidateIssue, str, str]] = []
    for item in scored_raw:
        url = item.get("issue", "")
        c = by_url.get(url)
        if c is None:
            continue
        fit_hint = item.get("fit_hint", "possible")
        reason = str(item.get("reason") or "").strip()
        scored.append((c, fit_hint, reason))

    # Any candidates the LLM dropped — append at the end with "possible"
    seen_urls = {c.url for c, _, _ in scored}
    for c in candidates:
        if c.url not in seen_urls:
            scored.append((c, "possible", ""))

    return _enforce_diversity(scored, top_n)
