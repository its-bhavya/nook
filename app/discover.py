"""
Discovery service for Nook.

Public entry point:
    discover_issues(profile, query, on_event) -> list[CandidateIssue]

Pipeline:
  1. Use LLM to build 2-3 targeted GitHub issue-search URLs.
  2. Run run_tinyfish on each URL in parallel (respecting concurrency limit).
  3. Validate, deduplicate, and cap at 15 candidates.
  4. Fall back to DISCOVERY_FALLBACK_URLS if GitHub extraction returns nothing.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

from app.config import get_settings
from app.llm import generate_json
from app.models import CandidateIssue, ClaimantSignal, IssueSource, Profile
from app.tinyfish import run_tinyfish

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/issues/\d+$",
    re.IGNORECASE,
)
_MAX_CANDIDATES = 15

# ---------------------------------------------------------------------------
# TinyFish output schema for search-results extraction
# ---------------------------------------------------------------------------

_SEARCH_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url":      {"type": "string"},
                    "title":    {"type": "string"},
                    "repo":     {"type": "string"},
                    "labels":   {"type": "array", "items": {"type": "string"}},
                    "opened":   {"type": "string", "nullable": True},
                    "comments": {"type": "integer", "nullable": True},
                    "assignee": {"type": "string", "nullable": True},
                    "claimant_signal": {
                        "type": "string",
                        "enum": ["none", "claimed", "linked_pr"],
                        "nullable": True,
                    },
                    "state": {"type": "string", "nullable": True},
                },
                "required": ["url", "title"],
            },
        }
    },
    "required": ["issues"],
}

_SEARCH_GOAL = (
    "List every visible open GitHub issue on this search results page. "
    "For each issue extract: the full issue URL, title, repo slug (owner/repo), "
    "all labels, the date opened, comment count, any visible assignee, "
    "and whether the issue appears claimed or has a linked PR. "
    "Only include issues that are shown as open. "
    "Return only what is visible on the page — do not follow any links."
)

# ---------------------------------------------------------------------------
# LLM prompt helpers
# ---------------------------------------------------------------------------

_URL_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["urls"],
}


def _build_search_prompt(profile: Profile, query: Optional[str]) -> str:
    skills = ", ".join(
        f"{s.name} ({s.level.value})" for s in profile.skills[:8]
    )
    interests = ", ".join(profile.interests[:5]) if profile.interests else "general open-source"
    query_line = f"Free-text query from the developer: \"{query}\"" if query else ""

    return f"""You are helping a developer find good-first-issue GitHub contributions.

Developer profile:
- Skills: {skills}
- Interests: {interests}
- Goals: {profile.goals}
- Hours per week: {profile.hours_per_week}
{query_line}

Produce exactly 2-3 GitHub issue search URLs that will find relevant open issues.
Rules:
- Each URL must use https://github.com/search?q=...&type=issues format.
- Every URL must include: is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22
- Target Python projects unless the query clearly specifies another language.
- Vary the terms across URLs — don't repeat the same keywords.
- Keep search terms focused and specific; prefer fewer terms over many.
- Return only the URLs, nothing else.

Return JSON: {{"urls": ["url1", "url2", ...]}}"""


# ---------------------------------------------------------------------------
# Fallback URL helpers
# ---------------------------------------------------------------------------

def _get_fallback_urls() -> list[str]:
    raw = get_settings().discovery_fallback_urls
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _is_valid_issue_url(url: Any) -> bool:
    return isinstance(url, str) and bool(_ISSUE_URL_RE.match(url))


def _repo_from_url(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _parse_claimant(raw: Any) -> ClaimantSignal:
    mapping = {
        "claimed": ClaimantSignal.claimed,
        "linked_pr": ClaimantSignal.linked_pr,
    }
    return mapping.get(str(raw or "").lower(), ClaimantSignal.none)


def _to_candidate(issue: dict, source: IssueSource) -> CandidateIssue | None:
    url = (issue.get("url") or "").strip()
    if not _is_valid_issue_url(url):
        return None

    state = str(issue.get("state") or "").lower()
    if state and state != "open":
        return None

    title = (issue.get("title") or "").strip()
    if not title:
        return None

    repo = (issue.get("repo") or _repo_from_url(url)).strip()
    labels = [str(l) for l in (issue.get("labels") or []) if l]
    comments = int(issue.get("comments") or 0)
    opened = issue.get("opened") or None

    return CandidateIssue(
        url=url,
        repo=repo,
        title=title,
        labels=labels,
        opened=str(opened) if opened else None,
        comments=comments,
        claimant_signal=_parse_claimant(issue.get("claimant_signal")),
        source=source,
    )


def _extract_candidates(tf_result, source: IssueSource) -> list[CandidateIssue]:
    if not tf_result.ok or not tf_result.parsed_result:
        return []

    raw = tf_result.parsed_result
    if not isinstance(raw, dict):
        return []

    candidates: list[CandidateIssue] = []
    for item in raw.get("issues") or []:
        c = _to_candidate(item, source)
        if c:
            candidates.append(c)
    return candidates


def _deduplicate(candidates: list[CandidateIssue]) -> list[CandidateIssue]:
    seen: set[str] = set()
    result: list[CandidateIssue] = []
    for c in candidates:
        if c.url not in seen:
            seen.add(c.url)
            result.append(c)
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def discover_issues(
    profile: Profile,
    query: Optional[str],
    on_event: Callable[[str], Coroutine | None] | None = None,
) -> list[CandidateIssue]:
    """
    Discover open GitHub issues matching the developer profile and optional query.

    Args:
        profile:   Structured developer profile.
        query:     Optional free-text search hint from the developer.
        on_event:  Optional async or sync callable for progress messages.

    Returns:
        Up to 15 validated CandidateIssue items.
    """

    async def _emit(msg: str) -> None:
        logger.info("discover_issues: %s", msg)
        if on_event:
            result = on_event(msg)
            if asyncio.iscoroutine(result):
                await result

    # ------------------------------------------------------------------
    # Step 1: Build search URLs via LLM
    # ------------------------------------------------------------------
    await _emit("Building searches")

    search_urls: list[str] = []
    llm_response = await generate_json(_build_search_prompt(profile, query), _URL_SCHEMA)

    if llm_response and isinstance(llm_response, dict):
        raw_urls = llm_response.get("urls") or []
        search_urls = [u for u in raw_urls if isinstance(u, str) and u.startswith("https://github.com/search")]
        logger.info("LLM produced %d search URLs", len(search_urls))
    else:
        logger.warning("LLM did not return URLs — falling back to hardcoded defaults")

    # Hardcoded fallback if LLM fails or is not configured
    if not search_urls:
        terms = "LLM+agents+OR+RAG"
        if query:
            words = re.sub(r"[^\w\s]", "", query).split()[:4]
            terms = "+".join(words)
        search_urls = [
            f"https://github.com/search?q={terms}+is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22+language%3Apython&type=issues",
            f"https://github.com/search?q=llm+agent+is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22+language%3Apython&type=issues",
        ]

    # ------------------------------------------------------------------
    # Step 2: Extract issues from each URL in parallel
    # ------------------------------------------------------------------
    await _emit("Searching GitHub")

    async def _scrape(url: str, source: IssueSource) -> list[CandidateIssue]:
        tf = await run_tinyfish(
            goal=_SEARCH_GOAL,
            url=url,
            output_schema=_SEARCH_OUTPUT_SCHEMA,
        )
        return _extract_candidates(tf, source)

    tasks = [_scrape(u, IssueSource.github_search) for u in search_urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[CandidateIssue] = []
    for r in results:
        if isinstance(r, list):
            candidates.extend(r)
        else:
            logger.warning("A search task failed: %s", r)

    # ------------------------------------------------------------------
    # Step 3: Validate and deduplicate
    # ------------------------------------------------------------------
    candidates = _deduplicate(candidates)

    # ------------------------------------------------------------------
    # Step 4: Fallback to aggregator if GitHub returned nothing
    # ------------------------------------------------------------------
    if not candidates:
        fallback_urls = _get_fallback_urls()
        if fallback_urls:
            await _emit("GitHub extraction returned nothing — trying aggregator fallback")
            agg_tasks = [_scrape(u, IssueSource.aggregator) for u in fallback_urls]
            agg_results = await asyncio.gather(*agg_tasks, return_exceptions=True)
            for r in agg_results:
                if isinstance(r, list):
                    candidates.extend(r)
            candidates = _deduplicate(candidates)
        else:
            logger.warning("No candidates found and no fallback URLs configured")

    # Cap at 15
    candidates = candidates[:_MAX_CANDIDATES]

    await _emit(f"Found {len(candidates)} candidates")
    await _emit("Validating")

    logger.info("discover_issues complete: %d candidates", len(candidates))
    return candidates
