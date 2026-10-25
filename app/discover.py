"""
Discovery service for Nook.

Public entry point:
    discover_issues(profile, query, on_event) -> list[CandidateIssue]

Pipeline:
  1. Derive themes from the profile (code, not LLM). Each theme uses exactly
     ONE keyword for GitHub search.
  2. GitHub REST API searches + TinyFish aggregator searches run in parallel.
  3. All searches share ONE global deadline (_DISCOVER_BUDGET_S). Each call gets
     whatever time is left, so a slow run can use as long as it needs without
     pushing the job past its limit.
  4. Results are written to a shared sink as soon as each call returns.
  5. Deduplicate, cap at 8 per search / 24 total before ranking.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote_plus, urlparse

import httpx

from app.config import get_settings
from app.models import CandidateIssue, ClaimantSignal, IssueSource, Profile, SkillLevel
from app.tinyfish import run_tinyfish

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ISSUE_URL_RE = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/issues/\d+$",
    re.IGNORECASE,
)
_MAX_PER_SEARCH = 8
_MAX_CANDIDATES = 24

# Global time budget for the whole discover phase — derived from config at runtime.
_DISCOVER_BUDGET_MARGIN_S = 40   # seconds reserved for ranking (LLM call) after searches finish

# Don't start any run with less than this left; it can't finish.
_MIN_RUN_S = 15

# ---------------------------------------------------------------------------
# TinyFish output schema — aggregator
# ---------------------------------------------------------------------------

_AGG_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url":              {"type": "string"},
                    "title":            {"type": "string"},
                    "repo":             {"type": "string"},
                    "primary_language": {"type": "string", "nullable": True},
                    "stars":            {"type": "integer", "nullable": True},
                    "labels":           {"type": "array", "items": {"type": "string"}},
                    "tech_stack":       {"type": "array", "items": {"type": "string"}},
                    "difficulty":       {"type": "string", "nullable": True},
                    "comments":         {"type": "integer", "nullable": True},
                    "opened":           {"type": "string", "nullable": True},
                    "summary":          {"type": "string", "nullable": True},
                },
                "required": ["url", "title"],
            },
        }
    },
    "required": ["issues"],
}

_AGG_RULES = (
    "Return at most 10 issues. "
    "Each 'url' MUST be the full GitHub issue link "
    "(https://github.com/<owner>/<repo>/issues/<number>), not a link to the aggregator site; "
    "use the card's 'View on GitHub' link if needed. "
    "Also return title, repo, primary_language, stars, labels, tech_stack, difficulty, "
    "comments, opened, and a one-line summary when visible. "
    "Wait for the list to load if it is slow. "
    "Do not open individual issues. Do not paginate. "
    "Skip claimed, closed, or assigned issues."
)

# ---------------------------------------------------------------------------
# Aggregator source table
# ---------------------------------------------------------------------------

def _gfi_org_url(language: str) -> str:
    return f"https://www.goodfirstissue.org/languages/{language.lower()}/topics/ai-infra-agents"

def _gfi_dev_url(language: str) -> str:
    return f"https://goodfirstissue.dev/language/{language.lower()}"

def _codetriage_url(language: str) -> str:
    return f"https://www.codetriage.com/?language={language}"

# (name, url_builder, goal)
_SOURCES: list[tuple[str, Any, str]] = [
    ("goodfirstissue.org", _gfi_org_url, _AGG_RULES),
    ("goodfirstissue.dev", _gfi_dev_url, _AGG_RULES),
    ("codetriage",         _codetriage_url, _AGG_RULES),
]

# ---------------------------------------------------------------------------
# Theme derivation — one keyword per theme
# ---------------------------------------------------------------------------

_AREA_HINTS: list[tuple[str, str]] = [
    ("fastapi", "backend/API"), ("flask", "backend/API"), ("django", "backend/API"),
    ("rest", "backend/API"), ("graphql", "backend/API"), ("api", "backend/API"),
    ("express", "backend/API"), ("node", "backend/API"),
    ("rag", "retrieval/vector-DB"), ("vector", "retrieval/vector-DB"),
    ("embed", "retrieval/vector-DB"), ("retriev", "retrieval/vector-DB"),
    ("chroma", "retrieval/vector-DB"), ("pinecone", "retrieval/vector-DB"),
    ("weaviate", "retrieval/vector-DB"), ("faiss", "retrieval/vector-DB"),
    ("milvus", "retrieval/vector-DB"), ("pgvector", "retrieval/vector-DB"),
    ("crewai", "LLM-agents"), ("langchain", "LLM-agents"), ("langgraph", "LLM-agents"),
    ("autogen", "LLM-agents"), ("agent", "LLM-agents"), ("llm", "LLM-agents"),
    ("openai", "LLM-agents"), ("gemini", "LLM-agents"), ("anthropic", "LLM-agents"),
    ("llama", "LLM-agents"),
    ("eval", "evaluation/testing"), ("test", "evaluation/testing"),
    ("benchmark", "evaluation/testing"), ("pytest", "evaluation/testing"),
    ("metric", "evaluation/testing"),
    ("nlp", "NLP/ML"), ("transformers", "NLP/ML"), ("huggingface", "NLP/ML"),
    ("pytorch", "NLP/ML"), ("tensorflow", "NLP/ML"), ("sklearn", "NLP/ML"),
    ("spacy", "NLP/ML"), ("nltk", "NLP/ML"),
    ("sql", "data/infra"), ("postgres", "data/infra"), ("mysql", "data/infra"),
    ("redis", "data/infra"), ("kafka", "data/infra"), ("spark", "data/infra"),
    ("pipeline", "data/infra"),
    ("react", "frontend/UI"), ("vue", "frontend/UI"), ("svelte", "frontend/UI"),
    ("typescript", "frontend/UI"), ("css", "frontend/UI"), ("ui", "frontend/UI"),
    ("frontend", "frontend/UI"),
    ("docker", "DevOps/tooling"), ("kubernetes", "DevOps/tooling"),
    ("cli", "DevOps/tooling"), ("tool", "DevOps/tooling"),
]

# Map area → best single keyword for GitHub search
_AREA_TO_KEYWORD: dict[str, str] = {
    "backend/API":         "FastAPI",
    "retrieval/vector-DB": "RAG",
    "LLM-agents":          "agent",
    "evaluation/testing":  "eval",
    "NLP/ML":              "transformers",
    "data/infra":          "pipeline",
    "frontend/UI":         "react",
    "DevOps/tooling":      "cli",
}

# Map area → goodfirstissue.org topic slug (hardcoded — slugs are static)
_AREA_TO_TOPIC: dict[str, str] = {
    "LLM-agents":          "ai-infra-agents",
    "retrieval/vector-DB": "ai-infra-agents",
    "NLP/ML":              "ai-infra-agents",
    "evaluation/testing":  "ai-infra-agents",
    "backend/API":         "ai-infra-agents",
    "data/infra":          "ai-infra-agents",
}

_SKIP_QUERY_WORDS = {
    "production", "small", "simple", "fix", "bug", "easy",
    "python", "the", "and",
}


def _area_for(token: str) -> str | None:
    t = token.lower()
    for fragment, area in _AREA_HINTS:
        if fragment in t:
            return area
    return None


def _detect_language(profile: Profile) -> str:
    lang_hints = [
        ("python", "Python"),
        ("typescript", "TypeScript"), ("javascript", "JavaScript"),
        ("java", "Java"), ("kotlin", "Kotlin"), ("go", "Go"),
        ("rust", "Rust"), ("c++", "C++"), ("c#", "C#"),
        ("ruby", "Ruby"), ("php", "PHP"),
    ]
    for s in profile.skills:
        if s.level == SkillLevel.inferred:
            continue
        name_l = s.name.lower()
        for fragment, lang in lang_hints:
            if fragment in name_l:
                return lang
    for s in profile.skills:
        name_l = s.name.lower()
        for fragment, lang in lang_hints:
            if fragment in name_l:
                return lang
    return "Python"


def derive_themes(profile: Profile, query: Optional[str]) -> list[tuple[str, str]]:
    """Return (theme_label, single_keyword) tuples — at most 2 themes."""
    slots: list[tuple[str, str]] = []
    seen_areas: set[str] = set()
    max_profile_slots = 2 if not query else 1

    ordered: list[str] = []
    for level in (SkillLevel.experienced, SkillLevel.familiar):
        for s in profile.skills:
            if s.level == level:
                ordered.append(s.name)
    ordered.extend(profile.interests)

    for token in ordered:
        if len(slots) >= max_profile_slots:
            break
        area = _area_for(token)
        if area is None:
            continue
        if area in seen_areas:
            continue
        seen_areas.add(area)
        keyword = _best_keyword(token, area)
        slots.append((area, keyword))

    if query:
        words = [w for w in re.split(r"[\s,]+", query) if len(w) > 2]
        meaningful = [w for w in words if w.lower() not in _SKIP_QUERY_WORDS]
        q_keyword = meaningful[0] if meaningful else (words[0] if words else query[:20])
        slots = [("query", q_keyword)] + slots

    return slots[:2]


def _best_keyword(token: str, area: str) -> str:
    """Pick the single best GitHub search keyword for a token/area combo."""
    mapped = _AREA_TO_KEYWORD.get(area)
    if mapped:
        return mapped
    clean = token.strip().split()[0]
    if len(clean) >= 3 and clean.lower() not in {"python", "javascript", "typescript", "the", "and"}:
        return clean
    return clean


# ---------------------------------------------------------------------------
# GitHub REST API search
# ---------------------------------------------------------------------------

async def _github_api_search(keyword: str | None, language: str) -> list[dict]:
    """Call GitHub REST search/issues and return raw issue dicts for _to_candidate."""
    parts = ['is:issue', 'is:open', 'label:"good first issue"', 'no:assignee', f'language:{language}']
    if keyword:
        parts.insert(0, keyword)
    q = " ".join(parts)

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"

    logger.info("GitHub API search: keyword=%r language=%s authenticated=%s", keyword, language, bool(token))

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://api.github.com/search/issues",
                params={"q": q, "sort": "updated", "order": "desc", "per_page": 10},
                headers=headers,
            )
        logger.info("GitHub API response: status=%d keyword=%r results=%d", resp.status_code, keyword, len((resp.json().get("items") or [])) if resp.status_code == 200 else 0)
        if resp.status_code != 200:
            logger.warning("GitHub API search returned %d for keyword=%r", resp.status_code, keyword)
            return []
        data = resp.json()
    except Exception as exc:
        logger.warning("GitHub API search error keyword=%r: %s", keyword, exc)
        return []

    results = []
    for item in data.get("items") or []:
        if "pull_request" in item:
            continue
        repo_url = item.get("repository_url", "")
        repo_parts = repo_url.rstrip("/").split("/")
        repo = f"{repo_parts[-2]}/{repo_parts[-1]}" if len(repo_parts) >= 2 else ""
        results.append({
            "url":             item.get("html_url", ""),
            "title":           item.get("title", ""),
            "repo":            repo,
            "labels":          [l["name"] for l in (item.get("labels") or [])],
            "opened":          item.get("created_at"),
            "comments":        item.get("comments", 0),
            "state":           item.get("state"),
            "claimant_signal": "none",
        })
    return results


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------

_REL_DATE_RE = re.compile(
    r"""
    (?:(\d+)\s+second|(\d+)\s+minute|(\d+)\s+hour
    |(\d+)\s+day|(\d+)\s+week|(\d+)\s+month|(\d+)\s+year)s?\s+ago
    |yesterday|today|just\s+now
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    if "T" in raw:
        return raw[:10]
    m = _REL_DATE_RE.search(raw)
    if m:
        today = date.today()
        seconds, minutes, hours, days, weeks, months, years = m.groups()
        if seconds or minutes or hours or (raw.lower() in ("today", "just now")):
            return today.isoformat()
        if raw.lower() == "yesterday":
            return (today - timedelta(days=1)).isoformat()
        if days:
            return (today - timedelta(days=int(days))).isoformat()
        if weeks:
            return (today - timedelta(weeks=int(weeks))).isoformat()
        if months:
            return (today - timedelta(days=int(months) * 30)).isoformat()
        if years:
            return (today - timedelta(days=int(years) * 365)).isoformat()
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return raw


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _is_valid_issue_url(url: Any) -> bool:
    return isinstance(url, str) and bool(_ISSUE_URL_RE.match(url))


def _repo_from_url(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _parse_claimant(raw: Any) -> ClaimantSignal:
    mapping = {"claimed": ClaimantSignal.claimed, "linked_pr": ClaimantSignal.linked_pr}
    return mapping.get(str(raw or "").lower(), ClaimantSignal.none)


def _to_candidate(issue: dict, source: IssueSource, theme: str) -> CandidateIssue | None:
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
    opened = _normalize_date(issue.get("opened"))

    claimed_flag = issue.get("claimed")
    if claimed_flag:
        claimant = ClaimantSignal.claimed
    else:
        claimant = _parse_claimant(issue.get("claimant_signal"))

    stars_raw = issue.get("stars")
    stars = int(stars_raw) if stars_raw is not None else None
    tech_stack = [str(t) for t in (issue.get("tech_stack") or []) if t]

    return CandidateIssue(
        url=url,
        repo=repo,
        title=title,
        labels=labels,
        opened=opened,
        comments=comments,
        claimant_signal=claimant,
        source=source,
        theme=theme,
        primary_language=issue.get("primary_language") or None,
        stars=stars,
        difficulty=issue.get("difficulty") or None,
        domain=issue.get("domain") or None,
        tech_stack=tech_stack,
        summary=issue.get("summary") or None,
    )


def _extract_candidates(
    tf_result,
    source: IssueSource,
    theme: str,
    max_per_search: int,
) -> list[CandidateIssue]:
    if not tf_result.ok or not tf_result.parsed_result:
        return []
    raw = tf_result.parsed_result
    if not isinstance(raw, dict):
        return []
    candidates: list[CandidateIssue] = []
    for item in (raw.get("issues") or []):
        c = _to_candidate(item, source, theme)
        if c:
            candidates.append(c)
        if len(candidates) >= max_per_search:
            break
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
# matched_skills computation
# ---------------------------------------------------------------------------

def compute_matched_skills(candidate: CandidateIssue, profile: Profile) -> list[str]:
    haystack = " ".join([
        candidate.title,
        candidate.repo,
        *candidate.labels,
        candidate.summary or "",
        candidate.domain or "",
        " ".join(candidate.tech_stack),
    ]).lower()
    matched = []
    for s in profile.skills:
        if s.name.lower() in haystack:
            matched.append(s.name)
    return matched


# ---------------------------------------------------------------------------
# Reason / validation helpers (kept for rank.py)
# ---------------------------------------------------------------------------

def _days_ago(opened: str | None) -> str | None:
    if not opened:
        return None
    try:
        dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - dt).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "1 day ago"
        return f"{delta} days ago"
    except Exception:
        return opened


def _fallback_reason(c: CandidateIssue) -> str:
    parts: list[str] = []
    if c.matched_skills:
        parts.append(f"Matches your {' and '.join(c.matched_skills[:3])} experience")
    elif c.theme:
        parts.append(f"Found via {c.theme} search")
    else:
        parts.append("Matches your profile")
    age = _days_ago(c.opened)
    if age:
        parts.append(f"opened {age}")
    if c.comments == 0:
        parts.append("no comments yet")
    elif c.comments == 1:
        parts.append("1 comment")
    else:
        parts.append(f"{c.comments} comments")
    return ", ".join(parts) + "."


_BAD_PHRASES = re.compile(r"\bgood first issue\b|\bbeginner\b", re.IGNORECASE)


def validate_reason(reason: str, c: CandidateIssue) -> bool:
    if not reason or not reason.strip():
        return False
    if _BAD_PHRASES.search(reason):
        return False
    if c.comments == 0 and re.search(r"\b(discussion|commented|comments)\b", reason, re.IGNORECASE):
        return False
    has_skill = any(s.lower() in reason.lower() for s in c.matched_skills) if c.matched_skills else False
    has_theme = bool(c.theme and c.theme.lower() in reason.lower())
    has_fact = bool(re.search(
        r"\b(\d+\s*comment|\d+\s*day|label|opened|recent|last\s*week|no\s*comment|star|\d+/10)\b",
        reason, re.IGNORECASE,
    ))
    return has_skill or has_theme or has_fact


# ---------------------------------------------------------------------------
# Deadline helpers
# ---------------------------------------------------------------------------

def _get_discover_budget() -> int:
    return max(60, get_settings().discover_timeout_s - _DISCOVER_BUDGET_MARGIN_S)


def _remaining(deadline: float) -> int:
    """Whole seconds left in the shared discover budget (never negative)."""
    return max(0, int(deadline - time.monotonic()))


def _call_timeout(deadline: float) -> int:
    """Each call may use whatever is left of the shared budget. No fixed cap."""
    return _remaining(deadline)


# ---------------------------------------------------------------------------
# Aggregator runner
# ---------------------------------------------------------------------------

async def _run_source(
    name: str,
    url: str,
    goal: str,
    theme: str,
    deadline: float,
    sink: list[CandidateIssue],
    on_event: Callable[[str], Coroutine | None] | None,
) -> None:
    if _remaining(deadline) < _MIN_RUN_S:
        logger.warning("[source/%s] skipped, no budget left", name)
        return

    logger.info("[source/%s] fetching %s", name, url)
    t0 = time.monotonic()

    result = await run_tinyfish(
        goal=goal,
        url=url,
        output_schema=_AGG_OUTPUT_SCHEMA,
        timeout_s=_call_timeout(deadline),
    )

    elapsed = time.monotonic() - t0
    candidates = _extract_candidates(result, IssueSource.aggregator, theme, _MAX_PER_SEARCH)
    sink.extend(candidates)

    logger.info(
        "[source/%s] results=%d elapsed=%.1fs error=%r",
        name, len(candidates), elapsed, result.error or None,
    )
    await _safe_emit(on_event, f"Agent searched {name}: {len(candidates)} issues")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

async def _safe_emit(
    on_event: Callable[[str], Coroutine | None] | None,
    msg: str,
) -> None:
    logger.info("discover: %s", msg)
    if on_event:
        result = on_event(msg)
        if asyncio.iscoroutine(result):
            await result


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

    GitHub REST API provides fast breadth; TinyFish agents browse aggregator
    sites in parallel. All searches share one deadline.
    Returns up to _MAX_CANDIDATES validated CandidateIssue items.
    """
    t_start = time.monotonic()
    budget = _get_discover_budget()
    deadline = t_start + budget

    language = _detect_language(profile)
    themes = derive_themes(profile, query)

    logger.info(
        "discover_issues: language=%s themes=%s budget=%ds",
        language, themes, budget,
    )

    sink: list[CandidateIssue] = []

    agg_theme = themes[0][0] if themes else "profile"
    agg_name, agg_url_builder, agg_goal = _SOURCES[0]

    # ------------------------------------------------------------------
    # Phase 1: TinyFish (one aggregator) + GitHub API — fired in parallel
    # ------------------------------------------------------------------
    await _safe_emit(on_event, "Searching GitHub and aggregators")

    gh_calls = [
        _github_api_search(keyword, language)
        for _, keyword in themes
    ] + [_github_api_search(None, language)]  # generic / label-only

    agg_task = asyncio.create_task(
        _run_source(agg_name, agg_url_builder(language), agg_goal, agg_theme, deadline, sink, on_event)
    )

    gh_results = await asyncio.gather(*gh_calls, return_exceptions=True)

    theme_labels = [label for label, _ in themes] + ["generic"]
    for theme_label, result in zip(theme_labels, gh_results):
        if isinstance(result, Exception):
            logger.warning("GitHub API search failed for theme=%s: %s", theme_label, result)
            continue
        for item in result:
            c = _to_candidate(item, IssueSource.github_search, theme_label)
            if c:
                sink.append(c)

    # Wait for the aggregator task (it was already running during the GitHub calls)
    try:
        await asyncio.wait_for(asyncio.shield(agg_task), timeout=_remaining(deadline))
    except (asyncio.TimeoutError, asyncio.CancelledError):
        agg_task.cancel()
        await asyncio.gather(agg_task, return_exceptions=True)
        await _safe_emit(on_event, "Aggregator search timed out, using GitHub results")

    # ------------------------------------------------------------------
    # Deduplicate, tag matched_skills, cap
    # ------------------------------------------------------------------
    candidates = _deduplicate(sink)
    for c in candidates:
        c.matched_skills = compute_matched_skills(c, profile)

    candidates = candidates[:_MAX_CANDIDATES]

    total_elapsed = time.monotonic() - t_start
    await _safe_emit(on_event, f"Found {len(candidates)} candidates in {total_elapsed:.1f}s")

    logger.info(
        "discover_issues complete: %d candidates, total_elapsed=%.1fs",
        len(candidates), total_elapsed,
    )
    return candidates
