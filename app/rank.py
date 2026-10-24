"""
Candidate ranker for Nook.

rank_candidates(profile, candidates, top_n=5) -> list[CandidateIssue]

Pipeline:
  1. Single LLM call scores every candidate 0–10 and returns matched_skills.
  2. Drop candidates scoring < 4 unless fewer than 3 would remain.
  3. Diversity: prefer claimant_signal=="none", at most 1 per repo,
     if > 2 final candidates share a theme swap lowest-scoring dups for
     best from unused themes (only when those score >= 5).
  4. Always return min(top_n, len(candidates)) results.
  5. Reason validation: weak/invalid reasons get a deterministic fallback.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict

from app.discover import _fallback_reason, validate_reason
from app.llm import generate_json
from app.models import CandidateIssue, ClaimantSignal, Profile

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM schema
# ---------------------------------------------------------------------------

_RANKING_SCHEMA = {
    "type": "object",
    "properties": {
        "scored": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue":          {"type": "string"},
                    "score":          {"type": "number"},
                    "matched_skills": {"type": "array", "items": {"type": "string"}},
                    "reason":         {"type": "string"},
                },
                "required": ["issue", "score", "matched_skills", "reason"],
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
            "primary_language": c.primary_language,
            "stars": c.stars,
            "difficulty": c.difficulty,
            "domain": c.domain,
            "tech_stack": c.tech_stack,
            "summary": c.summary,
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
- score: integer 0–10 for relevance to this developer's skills, interests, and goals
  (10 = perfect match, 0 = no overlap at all)
- matched_skills: list of profile skill names or interests this issue plausibly exercises
  (empty list if none — never invent skills)
- reason: one sentence (≤ 30 words) that:
    * mentions at least one matched_skill or theme AND one fact (comment count, age, label, score)
    * does NOT say "good first issue" or "beginner"
    * does NOT claim a discussion if comments is 0
    * does NOT describe content that was not provided
    * if scored 2 or below write "score: X/10" to be transparent

Return all {len(candidates)} candidates scored."""


# ---------------------------------------------------------------------------
# Selection with diversity
# ---------------------------------------------------------------------------

def _select(
    scored: list[tuple[CandidateIssue, float, list[str], str]],
    top_n: int,
) -> list[CandidateIssue]:
    """
    scored: (candidate, score, matched_skills, reason) sorted best-first.

    Rules:
    - Drop score < 4 unless that would leave < 3 candidates.
    - At most 1 per repo.
    - Prefer claimant_signal == none.
    - If > 2 share a theme in the final list, swap lowest-scoring dup
      for best unused-theme candidate scoring >= 5.
    - Always return min(top_n, len(scored)) results.
    """
    # Sort by (score desc, claimant none first)
    scored.sort(key=lambda x: (x[1], x[0].claimant_signal == ClaimantSignal.none), reverse=True)

    # Apply score floor
    above = [(c, sc, ms, r) for c, sc, ms, r in scored if sc >= 4]
    pool = above if len(above) >= 3 else scored

    # Greedy pick: 1 per repo
    repo_seen: set[str] = set()
    selected: list[tuple[CandidateIssue, float, list[str], str]] = []
    skipped: list[tuple[CandidateIssue, float, list[str], str]] = []

    for item in pool:
        c = item[0]
        if c.repo in repo_seen:
            skipped.append(item)
            continue
        repo_seen.add(c.repo)
        selected.append(item)
        if len(selected) >= top_n:
            break

    # Fill remaining slots from skipped (different repo already accepted above? no — repo_seen)
    # Actually skipped all had repo conflicts; try from remaining pool entries
    if len(selected) < top_n:
        all_repos = {item[0].repo for item in selected}
        for item in pool:
            if len(selected) >= top_n:
                break
            if item[0].repo not in all_repos and item not in selected:
                all_repos.add(item[0].repo)
                selected.append(item)

    # Theme diversity: if > 2 share a theme, swap duplicates
    theme_count: dict[str, int] = defaultdict(int)
    for c, _, _, _ in selected:
        theme_count[c.theme or "generic"] += 1

    over_themes = {t for t, cnt in theme_count.items() if cnt > 2}
    if over_themes:
        # Find candidates from unused themes scoring >= 5
        used_themes = {c.theme or "generic" for c, _, _, _ in selected}
        alternates = [
            item for item in pool
            if item not in selected
            and (item[0].theme or "generic") not in used_themes
            and item[1] >= 5
        ]
        for over_theme in over_themes:
            theme_items = [(i, item) for i, item in enumerate(selected)
                           if (item[0].theme or "generic") == over_theme]
            # Sort to find lowest scorer in over-represented theme
            theme_items.sort(key=lambda x: x[1][1])
            while (
                theme_count[over_theme] > 2
                and theme_items
                and alternates
            ):
                idx, worst = theme_items.pop(0)
                alt = alternates.pop(0)
                selected[idx] = alt
                theme_count[over_theme] -= 1
                theme_count[alt[0].theme or "generic"] += 1

    # Finalize: apply matched_skills + reason, enforce top_n
    final = selected[:top_n]
    result: list[CandidateIssue] = []
    for c, sc, ms, reason in final:
        c.score = round(sc, 1)
        c.matched_skills = ms if ms else c.matched_skills
        if not validate_reason(reason, c):
            logger.debug("Replacing invalid reason for %s", c.url)
            reason = _fallback_reason(c)
        c.reason = reason
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def rank_candidates(
    profile: Profile,
    candidates: list[CandidateIssue],
    top_n: int = 5,
) -> list[CandidateIssue]:
    """
    Score and rank candidates. Returns up to top_n with reason and score set.
    """
    if not candidates:
        return []

    prompt = _build_ranking_prompt(profile, candidates)
    result = await generate_json(prompt, _RANKING_SCHEMA, system=_SYSTEM_PROMPT)
    scored_raw: list[dict] = result.get("scored", []) if result else []

    by_url: dict[str, CandidateIssue] = {c.url: c for c in candidates}

    scored: list[tuple[CandidateIssue, float, list[str], str]] = []
    seen_urls: set[str] = set()

    for item in scored_raw:
        url = item.get("issue", "")
        c = by_url.get(url)
        if c is None:
            continue
        try:
            score = float(item.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        ms = [str(s) for s in (item.get("matched_skills") or [])]
        reason = str(item.get("reason") or "").strip()
        scored.append((c, score, ms, reason))
        seen_urls.add(url)

    # Candidates the LLM dropped — append with score 0
    for c in candidates:
        if c.url not in seen_urls:
            scored.append((c, 0.0, [], ""))

    target = min(top_n, len(candidates))
    return _select(scored, target)
