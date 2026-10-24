"""
Discovery service for Nook.

Public entry point:
    discover_issues(profile, query, on_event) -> list[CandidateIssue]

Pipeline:
  1. Derive themes from the profile (code, not LLM). Each theme uses exactly
     ONE keyword for GitHub search.
  2. GitHub searches + aggregator (goodfirstissue.org) run in parallel.
  3. All searches share ONE global deadline (_DISCOVER_BUDGET_S). Each call gets
     min(tinyfish_timeout, time remaining), so a slow run can use as long as it
     needs without pushing the job past its limit.
  4. Results are written to a shared sink as soon as each call returns, so when
     the deadline hits we keep everything gathered so far.
  5. If the themed GitHub searches return < 3 results, ONE shared label-only
     fallback runs (the URL is identical for every theme, so it runs once).
     It is skipped after a timeout or when too little budget remains.
  6. Validate, deduplicate, cap at 8 per search / 24 total before ranking.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import quote_plus, urlparse

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
# Keep this below DISCOVER_TIMEOUT_S by ~20s to leave room for ranking.
# See _get_discover_budget() below.
_DISCOVER_BUDGET_MARGIN_S = 20   # seconds reserved for ranking after searches finish

# Don't start a fallback run unless at least this much budget is left.
_MIN_FALLBACK_S = 45
# Don't start any run with less than this left; it can't finish.
_MIN_RUN_S = 15
_DISCOVER_BUDGET_MARGIN_S = 40   # reserved for ranking (LLM call) after searches finish

# ---------------------------------------------------------------------------
# TinyFish output schema — GitHub search
# ---------------------------------------------------------------------------

_SEARCH_OUTPUT_SCHEMA: dict = {
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
                    "labels":           {"type": "array", "items": {"type": "string"}},
                    "opened":           {"type": "string", "nullable": True},
                    "comments":         {"type": "integer", "nullable": True},
                    "claimant_signal":  {
                        "type": "string",
                        "enum": ["none", "claimed", "linked_pr"],
                        "nullable": True,
                    },
                    "state":            {"type": "string", "nullable": True},
                    "primary_language": {"type": "string", "nullable": True},
                    "stars":            {"type": "integer", "nullable": True},
                    "summary":          {"type": "string", "nullable": True},
                },
                "required": ["url", "title"],
            },
        }
    },
    "required": ["issues"],
}

# No step cap is available on our tier, so the goal text is the only lever on
# agent behaviour. Keep it explicit about not exploring and returning fast.
_SEARCH_GOAL = (
    "Extract the first 10 open GitHub issues listed on this search results page. "
    "For each issue return: url, title, repo (owner/repo), labels, opened date, "
    "comment count, claimant_signal (none/claimed/linked_pr), primary_language, "
    "stars (if visible), and a one-line summary of what the issue asks for. "
    "Do NOT scroll, paginate, click, or follow any links. "
    "Read ONLY what is visible on the initial page load, then return immediately. "
    "Do not wait for dynamic content — return as soon as the issue list is visible."
)

# ---------------------------------------------------------------------------
# TinyFish output schema — goodfirstissue.org aggregator
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
                    "difficulty":       {"type": "string", "nullable": True},
                    "domain":           {"type": "string", "nullable": True},
                    "tech_stack":       {"type": "array", "items": {"type": "string"}},
                    "claimed":          {"type": "boolean", "nullable": True},
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

_AGG_GOAL = (
    "Extract the first 10 open-source issues listed on this page. "
    "For each issue return: url, title, repo, primary_language, stars, "
    "difficulty, domain, tech_stack (list of strings), claimed (bool), "
    "comments, opened date, and a one-line summary. "
    "Do NOT scroll, paginate, click, or follow links. "
    "Read ONLY what is visible on the initial page load, then return immediately. "
    "Do not wait for dynamic content — return as soon as the issue list is visible."
)

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

# Known valid topic slugs on goodfirstissue.org/languages/python
_GFI_KNOWN_SLUGS: list[str] = ["ai-infra-agents"]

_GFI_BASE = "https://www.goodfirstissue.org"


def _area_for(token: str) -> str | None:
    t = token.lower()
    for fragment, area in _AREA_HINTS:
        if fragment in t:
            return area
    return None


def _detect_language(profile: Profile) -> str:
    """
    Return the developer's primary programming language.
    Python wins if present at experienced/familiar level.
    Other languages are detected in order; inferred-level skills are
    only considered if nothing higher-tier matches.
    """
    lang_hints = [
        ("python", "Python"),
        ("typescript", "TypeScript"), ("javascript", "JavaScript"),
        ("java", "Java"), ("kotlin", "Kotlin"), ("go", "Go"),
        ("rust", "Rust"), ("c++", "C++"), ("c#", "C#"),
        ("ruby", "Ruby"), ("php", "PHP"),
    ]
    # First pass: experienced or familiar
    for s in profile.skills:
        if s.level == SkillLevel.inferred:
            continue
        name_l = s.name.lower()
        for fragment, lang in lang_hints:
            if fragment in name_l:
                return lang
    # Second pass: inferred
    for s in profile.skills:
        name_l = s.name.lower()
        for fragment, lang in lang_hints:
            if fragment in name_l:
                return lang
    return "Python"


def derive_themes(profile: Profile, query: Optional[str]) -> list[tuple[str, str]]:
    """
    Return (theme_label, single_keyword) tuples — at most 2 themes.
    Each keyword is ONE word suitable for GitHub search.
    """
    slots: list[tuple[str, str]] = []
    seen_areas: set[str] = set()
    max_profile_slots = 2 if not query else 1

    # Ordered candidates: experienced → familiar → interests
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
        # Extract first meaningful word from query as keyword
        words = [w for w in re.split(r"[\s,]+", query) if len(w) > 2]
        q_keyword = words[0] if words else query[:20]
        slots = [("query", q_keyword)] + slots

    return slots[:2]


def _best_keyword(token: str, area: str) -> str:
    """Pick the single best GitHub search keyword for a token/area combo."""
    clean = token.strip().split()[0]  # first word only
    if len(clean) >= 3 and clean.lower() not in {"python", "javascript", "typescript", "the", "and"}:
        return clean
    return _AREA_TO_KEYWORD.get(area, clean)


# ---------------------------------------------------------------------------
# GitHub search URL builders
# ---------------------------------------------------------------------------

def _build_search_url(keyword: str, language: str) -> str:
    kw_enc = quote_plus(keyword)
    lang_enc = quote_plus(language)
    return (
        f"https://github.com/search?q={kw_enc}"
        f"+is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22"
        f"+language%3A{lang_enc}+no%3Aassignee"
        f"&type=issues&s=updated&o=desc"
    )


def _build_label_only_url(language: str) -> str:
    """Fallback: no keyword, just label + language + no:assignee."""
    lang_enc = quote_plus(language)
    return (
        f"https://github.com/search?q=is%3Aissue+is%3Aopen"
        f"+label%3A%22good+first+issue%22+language%3A{lang_enc}+no%3Aassignee"
        f"&type=issues&s=updated&o=desc"
    )


# ---------------------------------------------------------------------------
# Date normalisation: "5 days ago" → ISO date
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

    # Already ISO-ish (YYYY-MM-DD or contains 'T')
    if re.match(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]
    if "T" in raw:
        return raw[:10]

    m = _REL_DATE_RE.search(raw)
    if m:
        today = date.today()  # evaluated per call, not at import
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

    # Try common textual formats
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue

    # Give up — return raw so nothing is lost
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

    # Aggregator claimed flag → claimant_signal
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
# matched_skills computation (pure code — used as initial signal; LLM re-scores)
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
    """
    Return the wall-clock budget (seconds) for all searches combined.
    Reads DISCOVER_TIMEOUT_S from config and subtracts the ranking margin.
    """
    return max(60, get_settings().discover_timeout_s - _DISCOVER_BUDGET_MARGIN_S)


def _remaining(deadline: float) -> int:
    """Whole seconds left in the shared discover budget (never negative)."""
    return max(0, int(deadline - time.monotonic()))


def _call_timeout(deadline: float) -> int:
    """Each call may use whatever is left of the shared budget. No fixed cap."""
    return _remaining(deadline)


# ---------------------------------------------------------------------------
# Aggregator: fetch matching topic page using hardcoded slugs
# ---------------------------------------------------------------------------

def _pick_topic_slug(theme: str, available: list[str]) -> str | None:
    """Map a theme to the closest available topic slug."""
    default = _AREA_TO_TOPIC.get(theme)
    if not available:
        return default
    for s in available:
        if default and default in s:
            return s
    theme_words = set(re.split(r"[\W_]+", theme.lower()))
    for slug in available:
        slug_words = set(re.split(r"[\W_]+", slug.lower()))
        if theme_words & slug_words:
            return slug
    for slug in available:
        if "ai" in slug or "agent" in slug or "infra" in slug:
            return slug
    return available[0] if available else None


async def _run_aggregator_search(
    theme: str,
    topic_slugs: list[str],
    deadline: float,
    sink: list[CandidateIssue],
    on_event: Callable[[str], Coroutine | None] | None,
) -> None:
    slug = _pick_topic_slug(theme, topic_slugs)
    if not slug:
        logger.warning("No GFI topic slug found for theme %s", theme)
        return
    if _remaining(deadline) < _MIN_RUN_S:
        logger.warning("[aggregator/%s] skipped, no budget left", theme)
        return

    url = f"{_GFI_BASE}/languages/python/topics/{slug}"
    logger.info("[aggregator/%s] fetching %s", theme, url)
    t0 = time.monotonic()

    result = await run_tinyfish(
        goal=_AGG_GOAL,
        url=url,
        output_schema=_AGG_OUTPUT_SCHEMA,
        timeout_s=_call_timeout(deadline),
    )

    elapsed = time.monotonic() - t0
    candidates = _extract_candidates(result, IssueSource.aggregator, theme, _MAX_PER_SEARCH)
    sink.extend(candidates)  # visible immediately, even if we're cancelled later

    logger.info(
        "[aggregator/%s] url=%s results=%d elapsed=%.1fs error=%r",
        theme, url, len(candidates), elapsed, result.error or None,
    )
    await _safe_emit(on_event, f"Searched {theme} (aggregator): {len(candidates)} issues")


# ---------------------------------------------------------------------------
# GitHub search runner with a single shared fallback
# ---------------------------------------------------------------------------

async def _run_github_search(
    theme: str,
    keyword: str,
    language: str,
    deadline: float,
    sink: list[CandidateIssue],
    state: dict[str, bool],
    on_event: Callable[[str], Coroutine | None] | None,
) -> None:
    if _remaining(deadline) < _MIN_RUN_S:
        logger.warning("[github/%s] skipped, no budget left", theme)
        return

    url = _build_search_url(keyword, language)
    t0 = time.monotonic()
    logger.info("[github/%s] keyword=%r url=%s", theme, keyword, url)

    result = await run_tinyfish(
        goal=_SEARCH_GOAL,
        url=url,
        output_schema=_SEARCH_OUTPUT_SCHEMA,
        timeout_s=_call_timeout(deadline),
    )

    elapsed = time.monotonic() - t0
    candidates = _extract_candidates(result, IssueSource.github_search, theme, _MAX_PER_SEARCH)
    sink.extend(candidates)  # visible immediately, even if we're cancelled later
    count = len(candidates)

    logger.info(
        "[github/%s] keyword=%r results=%d elapsed=%.1fs error=%r",
        theme, keyword, count, elapsed, result.error or None,
    )

    # Label-only fallback: URL is identical for every theme, so run it at most
    # once. Skip after a timeout (it would likely time out too) or when there
    # isn't enough budget left to finish.
    timed_out = "timeout" in (result.error or "").lower()
    if (
        count < 3
        and not timed_out
        and not state["fallback_started"]
        and _remaining(deadline) > _MIN_FALLBACK_S
    ):
        state["fallback_started"] = True
        logger.info("[github/%s] only %d results — running label-only fallback", theme, count)
        t1 = time.monotonic()
        retry = await run_tinyfish(
            goal=_SEARCH_GOAL,
            url=_build_label_only_url(language),
            output_schema=_SEARCH_OUTPUT_SCHEMA,
            timeout_s=_call_timeout(deadline),
        )
        extra = _extract_candidates(retry, IssueSource.github_search, theme, _MAX_PER_SEARCH)
        sink.extend(extra)
        count += len(extra)
        logger.info(
            "[github/%s] label-only fallback results=%d elapsed=%.1fs error=%r",
            theme, len(extra), time.monotonic() - t1, retry.error or None,
        )

    await _safe_emit(on_event, f"Searched {theme}: {count} issues")


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

    All searches run in parallel under one shared deadline. Whatever has been
    collected when the deadline hits is returned.
    Returns up to _MAX_CANDIDATES validated CandidateIssue items.
    """
    t_start = time.monotonic()
    budget = _get_discover_budget()
    deadline = t_start + budget
    await _safe_emit(on_event, "Building searches")

    language = _detect_language(profile)
    themes = derive_themes(profile, query)

    logger.info(
        "discover_issues: language=%s themes=%s budget=%ds",
        language, themes, budget,
    )

    # Shared state: tasks append results here as soon as each TinyFish call returns
    sink: list[CandidateIssue] = []
    state = {"fallback_started": False}

    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            _run_github_search(label, keyword, language, deadline, sink, state, on_event)
        )
        for label, keyword in themes
    ]
    # One aggregator search is enough
    if themes:
        tasks.append(
            asyncio.create_task(
                _run_aggregator_search(themes[0][0], _GFI_KNOWN_SLUGS, deadline, sink, on_event)
            )
        )

    # -----------------------------------------------------------------------
    # Wait until everything finishes OR the budget expires, whichever is first.
    # Pending tasks are cancelled; anything already in `sink` is kept.
    # -----------------------------------------------------------------------
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=_remaining(deadline) + 5)

        if pending:
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await _safe_emit(
                on_event,
                f"Time budget reached, using results from {len(done)}/{len(tasks)} finished searches",
            )

        for t in done:
            if not t.cancelled() and t.exception():
                logger.warning("Search task failed: %s", t.exception())

    # -----------------------------------------------------------------------
    # Deduplicate, tag matched_skills, cap
    # -----------------------------------------------------------------------
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