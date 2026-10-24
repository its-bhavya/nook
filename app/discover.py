"""
Discovery service for Nook.

Public entry point:
    discover_issues(profile, query, on_event) -> list[CandidateIssue]

Pipeline:
  1. Derive up to 3 distinct themes from the profile (code, not LLM).
  2. Build one GitHub search URL per theme; run all in parallel.
  3. Tag each candidate with theme + matched_skills (code, not LLM).
  4. Validate, deduplicate, cap at 8 per search / 24 total.
  5. Fall back to DISCOVERY_FALLBACK_URLS if GitHub returned nothing.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
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
_MAX_CANDIDATES = 24  # before ranking; ranking trims to 5

# ---------------------------------------------------------------------------
# TinyFish output schema
# ---------------------------------------------------------------------------

_SEARCH_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url":             {"type": "string"},
                    "title":           {"type": "string"},
                    "repo":            {"type": "string"},
                    "labels":          {"type": "array", "items": {"type": "string"}},
                    "opened":          {"type": "string", "nullable": True},
                    "comments":        {"type": "integer", "nullable": True},
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
    "Extract all open GitHub issues listed on this search results page. "
    "For each issue return: url, title, repo (owner/repo), labels, opened date, "
    "comment count, and claimant_signal (none/claimed/linked_pr). "
    "Do not follow any links. Read only what is visible on this page."
)

_SCRAPE_TIMEOUT_S = 45

# ---------------------------------------------------------------------------
# Theme derivation (pure code — no LLM)
# ---------------------------------------------------------------------------

# Broad area buckets: map lowercase skill/interest keyword fragments to a canonical area label.
# First match wins. Keep this short; it only needs to separate obviously different domains.
_AREA_HINTS: list[tuple[str, str]] = [
    # Web / API
    ("fastapi", "backend/API"), ("flask", "backend/API"), ("django", "backend/API"),
    ("rest", "backend/API"), ("graphql", "backend/API"), ("api", "backend/API"),
    ("express", "backend/API"), ("node", "backend/API"),
    # Retrieval / vector
    ("rag", "retrieval/vector-DB"), ("vector", "retrieval/vector-DB"),
    ("embed", "retrieval/vector-DB"), ("retriev", "retrieval/vector-DB"),
    ("chroma", "retrieval/vector-DB"), ("pinecone", "retrieval/vector-DB"),
    ("weaviate", "retrieval/vector-DB"), ("faiss", "retrieval/vector-DB"),
    ("milvus", "retrieval/vector-DB"), ("pgvector", "retrieval/vector-DB"),
    # LLM orchestration / agents
    ("crewai", "LLM-agents"), ("langchain", "LLM-agents"), ("langgraph", "LLM-agents"),
    ("autogen", "LLM-agents"), ("agent", "LLM-agents"), ("llm", "LLM-agents"),
    ("openai", "LLM-agents"), ("gemini", "LLM-agents"), ("anthropic", "LLM-agents"),
    ("llama", "LLM-agents"),
    # Evaluation / testing
    ("eval", "evaluation/testing"), ("test", "evaluation/testing"),
    ("benchmark", "evaluation/testing"), ("pytest", "evaluation/testing"),
    ("metric", "evaluation/testing"), ("assert", "evaluation/testing"),
    # NLP / ML
    ("nlp", "NLP/ML"), ("transformers", "NLP/ML"), ("huggingface", "NLP/ML"),
    ("pytorch", "NLP/ML"), ("tensorflow", "NLP/ML"), ("sklearn", "NLP/ML"),
    ("spacy", "NLP/ML"), ("nltk", "NLP/ML"), ("ml", "NLP/ML"),
    # Data / infra
    ("sql", "data/infra"), ("postgres", "data/infra"), ("mysql", "data/infra"),
    ("redis", "data/infra"), ("kafka", "data/infra"), ("spark", "data/infra"),
    ("data", "data/infra"), ("pipeline", "data/infra"),
    # Frontend / UI
    ("react", "frontend/UI"), ("vue", "frontend/UI"), ("svelte", "frontend/UI"),
    ("typescript", "frontend/UI"), ("css", "frontend/UI"), ("ui", "frontend/UI"),
    ("frontend", "frontend/UI"),
    # DevOps / tooling
    ("docker", "DevOps/tooling"), ("kubernetes", "DevOps/tooling"), ("k8s", "DevOps/tooling"),
    ("ci", "DevOps/tooling"), ("cd", "DevOps/tooling"), ("github action", "DevOps/tooling"),
    ("cli", "DevOps/tooling"), ("tool", "DevOps/tooling"),
]


def _area_for(token: str) -> str | None:
    t = token.lower()
    for fragment, area in _AREA_HINTS:
        if fragment in t:
            return area
    return None


def _detect_language(profile: Profile) -> str:
    """Best-guess primary language from skill names."""
    lang_hints = [
        ("typescript", "TypeScript"), ("javascript", "JavaScript"),
        ("java", "Java"), ("kotlin", "Kotlin"), ("go", "Go"),
        ("rust", "Rust"), ("c++", "C++"), ("c#", "C#"),
        ("ruby", "Ruby"), ("php", "PHP"),
    ]
    for s in profile.skills:
        name_l = s.name.lower()
        for fragment, lang in lang_hints:
            if fragment in name_l:
                return lang
    return "Python"  # default


def _skill_search_keywords(skill_name: str) -> str:
    """Turn a skill name into compact GitHub search keywords."""
    # Strip common noise words
    noise = {"python", "javascript", "typescript"}
    tokens = [t for t in re.split(r"[\s/\-_]+", skill_name.lower()) if t not in noise and len(t) > 1]
    return "+".join(tokens[:3]) if tokens else skill_name.lower().replace(" ", "+")


def derive_themes(profile: Profile, query: Optional[str]) -> list[tuple[str, str]]:
    """
    Return a list of (theme_label, search_keyword_string) tuples.

    Rules:
    - If query provided: first slot = ("query", url-encoded query terms).
    - Fill remaining slots (up to 3 total) from profile skills in order:
      experienced → familiar, then interests.
    - Each slot must map to a *different* area bucket (no duplicates).
    - Do not use a skill as a theme if it appears only once across the whole
      profile AND the user did not type it in the query.
    - Always append one generic good-first-issue slot for the main language.
    """
    slots: list[tuple[str, str]] = []
    seen_areas: set[str] = set()
    max_profile_slots = 2 if query else 3

    # Count how many times each skill name appears (skills list can have duplicates
    # if the LLM repeated itself; also count via evidence mentions is not reliable,
    # so we count by name occurrences across skills list).
    skill_count: dict[str, int] = {}
    for s in profile.skills:
        key = s.name.lower()
        skill_count[key] = skill_count.get(key, 0) + 1

    # Build ordered candidate tokens: experienced skills first, then familiar, then interests
    ordered: list[tuple[str, str]] = []  # (token, level_or_interest)
    for level in (SkillLevel.experienced, SkillLevel.familiar):
        for s in profile.skills:
            if s.level == level:
                ordered.append((s.name, level.value))
    for interest in profile.interests:
        ordered.append((interest, "interest"))

    for token, origin in ordered:
        if len(slots) >= max_profile_slots:
            break
        area = _area_for(token)
        if area is None:
            continue
        if area in seen_areas:
            continue
        # Skip single-occurrence skills (unless user typed it in the query)
        if origin in ("experienced", "familiar"):
            count = skill_count.get(token.lower(), 1)
            in_query = query and token.lower() in query.lower()
            if count == 1 and not in_query:
                # Only skip if this area is not unique in the whole profile
                # (i.e., there's another skill that maps to a different area we can use instead)
                other_areas = {_area_for(s.name) for s in profile.skills if s.name.lower() != token.lower()}
                remaining_areas = other_areas - seen_areas - {None}
                if remaining_areas:
                    continue  # skip, we have better options
        seen_areas.add(area)
        slots.append((area, _skill_search_keywords(token)))

    # Query slot goes first
    if query:
        q_keywords = re.sub(r"[^\w\s]", "", query)
        q_keywords = "+".join(q_keywords.split()[:5])
        slots = [("query", q_keywords)] + slots

    return slots


# ---------------------------------------------------------------------------
# Search URL builder
# ---------------------------------------------------------------------------

def _build_search_url(keywords: str, language: str) -> str:
    lang_enc = quote_plus(language)
    base = (
        f"https://github.com/search?q={keywords}"
        f"+is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22"
        f"+language%3A{lang_enc}"
        f"&type=issues&s=updated&o=desc"
    )
    return base


def _build_generic_url(language: str) -> str:
    lang_enc = quote_plus(language)
    return (
        f"https://github.com/search?q=is%3Aissue+is%3Aopen"
        f"+label%3A%22good+first+issue%22+language%3A{lang_enc}"
        f"&type=issues&s=updated&o=desc"
    )


# ---------------------------------------------------------------------------
# matched_skills computation (pure code — no LLM)
# ---------------------------------------------------------------------------

def compute_matched_skills(candidate: CandidateIssue, profile: Profile) -> list[str]:
    """Case-insensitive match of profile skill names against title + labels + repo."""
    haystack = " ".join([
        candidate.title,
        candidate.repo,
        *candidate.labels,
    ]).lower()
    matched = []
    for s in profile.skills:
        if s.name.lower() in haystack:
            matched.append(s.name)
    return matched


# ---------------------------------------------------------------------------
# Reason validation and fallback
# ---------------------------------------------------------------------------

def _days_ago(opened: str | None) -> str | None:
    if not opened:
        return None
    try:
        dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        delta = (datetime.now(timezone.utc) - dt).days
        if delta == 0:
            return "today"
        if delta == 1:
            return "1 day ago"
        return f"{delta} days ago"
    except Exception:
        return opened


def _fallback_reason(c: CandidateIssue) -> str:
    """Build a deterministic reason from structured fields."""
    parts: list[str] = []

    if c.matched_skills:
        skills_str = " and ".join(c.matched_skills[:3])
        parts.append(f"Matches your {skills_str} experience")
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


_BAD_PHRASES = re.compile(
    r"\bgood first issue\b|\bbeginner\b",
    re.IGNORECASE,
)


def validate_reason(reason: str, c: CandidateIssue) -> bool:
    """
    Return True if the reason is acceptable.
    Fail if:
    - uses banned phrases ("good first issue", "beginner")
    - claims a discussion but comments == 0
    - mentions neither a matched skill/theme nor a candidate fact
    """
    if not reason or not reason.strip():
        return False
    if _BAD_PHRASES.search(reason):
        return False
    # Must not claim discussion/comments when there are none
    if c.comments == 0 and re.search(r"\b(discussion|commented|comments)\b", reason, re.IGNORECASE):
        return False
    # Must mention at least one grounding signal
    has_skill = any(s.lower() in reason.lower() for s in c.matched_skills) if c.matched_skills else False
    has_theme = bool(c.theme and c.theme.lower() in reason.lower())
    has_fact = bool(re.search(
        r"\b(\d+\s*comment|\d+\s*day|label|opened|recent|last\s*week|no\s*comment)\b",
        reason, re.IGNORECASE,
    ))
    if not (has_skill or has_theme or has_fact):
        return False
    return True


# ---------------------------------------------------------------------------
# Fallback URL helpers
# ---------------------------------------------------------------------------

def _get_fallback_urls() -> list[str]:
    raw = get_settings().discovery_fallback_urls
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


# ---------------------------------------------------------------------------
# Validation / parsing helpers
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
        theme=theme,
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
# Public entry point
# ---------------------------------------------------------------------------

async def discover_issues(
    profile: Profile,
    query: Optional[str],
    on_event: Callable[[str], Coroutine | None] | None = None,
) -> list[CandidateIssue]:
    """
    Discover open GitHub issues matching the developer profile and optional query.

    Returns up to _MAX_CANDIDATES validated CandidateIssue items, each tagged
    with theme and matched_skills.
    """

    async def _emit(msg: str) -> None:
        logger.info("discover_issues: %s", msg)
        if on_event:
            result = on_event(msg)
            if asyncio.iscoroutine(result):
                await result

    # ------------------------------------------------------------------
    # Step 1: Derive themes and build search URLs (pure code)
    # ------------------------------------------------------------------
    await _emit("Building searches")

    language = _detect_language(profile)
    themes = derive_themes(profile, query)

    # Build one URL per theme; always add a generic fallback slot
    search_tasks: list[tuple[str, str, IssueSource]] = []  # (theme, url, source)
    for theme_label, keywords in themes:
        url = _build_search_url(keywords, language)
        search_tasks.append((theme_label, url, IssueSource.github_search))

    # Generic good-first-issue search for the main language (always included)
    search_tasks.append(("generic", _build_generic_url(language), IssueSource.github_search))

    logger.info(
        "discover_issues: %d search tasks, language=%s, themes=%s",
        len(search_tasks), language, [t[0] for t in search_tasks],
    )

    # ------------------------------------------------------------------
    # Step 2: Scrape all URLs in parallel
    # ------------------------------------------------------------------
    await _emit("Searching GitHub")

    async def _scrape(theme: str, url: str, source: IssueSource) -> list[CandidateIssue]:
        tf = await run_tinyfish(
            goal=_SEARCH_GOAL,
            url=url,
            output_schema=_SEARCH_OUTPUT_SCHEMA,
            timeout_s=_SCRAPE_TIMEOUT_S,
        )
        return _extract_candidates(tf, source, theme, _MAX_PER_SEARCH)

    raw_results = await asyncio.gather(
        *[_scrape(t, u, s) for t, u, s in search_tasks],
        return_exceptions=True,
    )

    candidates: list[CandidateIssue] = []
    for i, r in enumerate(raw_results):
        if isinstance(r, list):
            candidates.extend(r)
        else:
            logger.warning("Search task %d (%s) failed: %s", i, search_tasks[i][0], r)

    # ------------------------------------------------------------------
    # Step 3: Tag matched_skills (pure code), deduplicate, cap
    # ------------------------------------------------------------------
    candidates = _deduplicate(candidates)
    for c in candidates:
        c.matched_skills = compute_matched_skills(c, profile)

    # ------------------------------------------------------------------
    # Step 4: Fallback to aggregator if GitHub returned nothing
    # ------------------------------------------------------------------
    if not candidates:
        fallback_urls = _get_fallback_urls()
        if fallback_urls:
            await _emit("GitHub extraction returned nothing — trying aggregator fallback")
            agg_results = await asyncio.gather(
                *[_scrape("fallback", u, IssueSource.aggregator) for u in fallback_urls],
                return_exceptions=True,
            )
            for r in agg_results:
                if isinstance(r, list):
                    candidates.extend(r)
            candidates = _deduplicate(candidates)
            for c in candidates:
                c.matched_skills = compute_matched_skills(c, profile)
        else:
            logger.warning("No candidates found and no fallback URLs configured")

    candidates = candidates[:_MAX_CANDIDATES]

    await _emit(f"Found {len(candidates)} candidates")
    await _emit("Validating")

    logger.info("discover_issues complete: %d candidates", len(candidates))
    return candidates
