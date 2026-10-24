"""
Investigation service for Nook.

Public entry point:
    investigate_issue(issue_url, on_event) -> EvidenceBundle

Two-path pipeline
-----------------
1. FAST PATH  — TinyFish Fetch API fetches the issue page + any linked PR pages
   in parallel (~5-15 s, free tier).  The LLM extracts structured findings from
   the raw text.  If the result has ≥3 findings we return immediately.

2. AGENT PATH — Full TinyFish agent run (up to INVESTIGATE_TIMEOUT_S seconds).
   Used when the fast path yields < 3 findings (repo-activity data, discussion
   on other pages, etc.).  The agent goal is narrowed to only what the fetch
   could not answer.  If the fast path already got something, the agent result
   is merged in and the bundle is marked partial=False.

Never raises — partial or failed results come back with partial=True and a
populated unknowns list.  An empty bundle (0 findings) is returned as-is so
callers can gate on it.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.tinyfish import FetchPageResult, fetch_pages, run_tinyfish

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

class IssueHeader(BaseModel):
    url: str
    title: Optional[str] = None
    state: Optional[str] = None                 # "open" | "closed" | unknown
    labels: list[str] = Field(default_factory=list)
    opened: Optional[str] = None
    author_language_notes: Optional[str] = None  # e.g. "thread is in Chinese"


class Finding(BaseModel):
    claim: str
    source_url: Optional[str] = None
    kind: str  # requirement | blocker | prerequisite | status | activity | related_pr


class RelatedPR(BaseModel):
    url: str
    state: str   # open | merged | closed | unknown
    note: Optional[str] = None


class ClaimSignal(BaseModel):
    who: Optional[str] = None
    when: Optional[str] = None
    source_url: Optional[str] = None


class ActivitySignal(BaseModel):
    signal: str
    source_url: Optional[str] = None


class EvidenceBundle(BaseModel):
    issue: IssueHeader
    findings: list[Finding] = Field(default_factory=list)
    related_prs: list[RelatedPR] = Field(default_factory=list)
    claim_signals: list[ClaimSignal] = Field(default_factory=list)
    activity_signals: list[ActivitySignal] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    already_resolved: bool = False
    already_resolved_url: Optional[str] = None
    partial: bool = False
    elapsed_s: float = 0.0
    path_used: str = "agent"   # "fetch" | "fetch+agent" | "agent"


# ---------------------------------------------------------------------------
# TinyFish agent goal and output_schema
# ---------------------------------------------------------------------------

_AGENT_GOAL_FULL = (
    "Investigate this GitHub issue. "
    "Identify relevant pull requests (with their state: open, merged, or closed), "
    "discussion that changes the scope or status of the issue, "
    "documentation, prerequisites, possible blockers, "
    "signs that someone has claimed it or that a fix has already merged, "
    "and repository activity signals such as recent maintainer replies and recent commits. "
    "Return supporting source URLs for every finding and explicitly list anything "
    "you cannot verify."
)

_AGENT_GOAL_NARROW = (
    "The issue page has already been read. "
    "Focus ONLY on: (1) repository activity signals — recent commits, maintainer replies, "
    "release activity, PR flow; (2) any discussion or comments NOT on the issue page itself. "
    "Do NOT re-read the issue body. "
    "Return supporting source URLs for every finding."
)

# TinyFish output_schema — must follow the strict allowlist rules
_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issue": {
            "type": "object",
            "properties": {
                "url":                  {"type": "string"},
                "title":                {"type": "string",  "nullable": True},
                "state":                {"type": "string",  "nullable": True},
                "labels":               {"type": "array",   "items": {"type": "string"}},
                "opened":               {"type": "string",  "nullable": True},
                "author_language_notes":{"type": "string",  "nullable": True},
            },
            "required": ["url"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim":      {"type": "string"},
                    "source_url": {"type": "string", "nullable": True},
                    "kind": {
                        "type": "string",
                        "enum": ["requirement", "blocker", "prerequisite",
                                 "status", "activity", "related_pr"],
                    },
                },
                "required": ["claim", "kind"],
            },
        },
        "related_prs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url":   {"type": "string"},
                    "state": {"type": "string",
                              "enum": ["open", "merged", "closed", "unknown"]},
                    "note":  {"type": "string", "nullable": True},
                },
                "required": ["url", "state"],
            },
        },
        "claim_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who":        {"type": "string", "nullable": True},
                    "when":       {"type": "string", "nullable": True},
                    "source_url": {"type": "string", "nullable": True},
                },
            },
        },
        "activity_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal":     {"type": "string"},
                    "source_url": {"type": "string", "nullable": True},
                },
                "required": ["signal"],
            },
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["issue", "findings"],
}

# ---------------------------------------------------------------------------
# URL validation helpers
# ---------------------------------------------------------------------------

_GITHUB_PATTERN = re.compile(
    r"^https://(github\.com|raw\.githubusercontent\.com|gist\.github\.com)/",
    re.IGNORECASE,
)

_DOC_DOMAINS = {
    "docs.github.com", "docs.python.org", "pypi.org", "packaging.python.org",
    "readthedocs.io", "readthedocs.org", "rtfd.io",
}

_ISSUE_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/issues/\d+",
    re.IGNORECASE,
)

_PR_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/pull/\d+",
    re.IGNORECASE,
)


def _repo_slug(issue_url: str) -> str:
    parts = urlparse(issue_url).path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _is_valid_source(url: Any, repo: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if any(domain == d or domain.endswith("." + d) for d in _DOC_DOMAINS):
        return True
    if not _GITHUB_PATTERN.match(url):
        return False
    if domain == "github.com" and repo:
        path = parsed.path.strip("/")
        if not path.startswith(repo):
            logger.debug("Cross-repo source URL accepted: %s (issue repo: %s)", url, repo)
    return True


# ---------------------------------------------------------------------------
# Core validation logic
# ---------------------------------------------------------------------------

def _validate_and_clean(
    raw: dict,
    issue_url: str,
) -> tuple[EvidenceBundle, list[str]]:
    warnings: list[str] = []
    repo = _repo_slug(issue_url)

    raw_issue = raw.get("issue") or {}
    issue = IssueHeader(
        url=raw_issue.get("url") or issue_url,
        title=raw_issue.get("title"),
        state=raw_issue.get("state"),
        labels=raw_issue.get("labels") or [],
        opened=raw_issue.get("opened"),
        author_language_notes=raw_issue.get("author_language_notes"),
    )

    clean_findings: list[Finding] = []
    for f in raw.get("findings") or []:
        claim = (f.get("claim") or "").strip()
        if not claim:
            continue
        src = f.get("source_url")
        kind = f.get("kind") or "status"
        if _is_valid_source(src, repo):
            clean_findings.append(Finding(claim=claim, source_url=src, kind=kind))
        else:
            warnings.append(f"Unsourced finding moved to unknowns: \"{claim}\"")

    clean_prs: list[RelatedPR] = []
    for pr in raw.get("related_prs") or []:
        url = pr.get("url") or ""
        if _is_valid_source(url, repo):
            clean_prs.append(RelatedPR(
                url=url,
                state=pr.get("state") or "unknown",
                note=pr.get("note"),
            ))
        else:
            warnings.append(f"Related PR dropped (invalid URL): \"{url}\"")

    clean_claims: list[ClaimSignal] = []
    for cs in raw.get("claim_signals") or []:
        src = cs.get("source_url")
        clean_claims.append(ClaimSignal(
            who=cs.get("who"),
            when=cs.get("when"),
            source_url=src if _is_valid_source(src, repo) else None,
        ))

    clean_activity: list[ActivitySignal] = []
    for sig in raw.get("activity_signals") or []:
        signal = (sig.get("signal") or "").strip()
        if not signal:
            continue
        src = sig.get("source_url")
        clean_activity.append(ActivitySignal(
            signal=signal,
            source_url=src if _is_valid_source(src, repo) else None,
        ))

    unknowns: list[str] = list(raw.get("unknowns") or [])
    unknowns.extend(warnings)

    already_resolved = False
    resolved_url: str | None = None
    issue_state_closed = (issue.state or "").lower() == "closed"
    merged_pr = next((pr for pr in clean_prs if pr.state == "merged"), None)
    fix_finding = next(
        (f for f in clean_findings
         if f.kind == "status"
         and any(kw in f.claim.lower() for kw in ("merged", "already fixed", "fix merged", "closed"))),
        None,
    )
    if merged_pr:
        already_resolved = True
        resolved_url = merged_pr.url
    elif issue_state_closed:
        already_resolved = True
        resolved_url = issue.url
    elif fix_finding:
        already_resolved = True
        resolved_url = fix_finding.source_url or issue.url

    return EvidenceBundle(
        issue=issue,
        findings=clean_findings,
        related_prs=clean_prs,
        claim_signals=clean_claims,
        activity_signals=clean_activity,
        unknowns=unknowns,
        already_resolved=already_resolved,
        already_resolved_url=resolved_url,
        partial=False,
    ), warnings


# ---------------------------------------------------------------------------
# Fast path: Fetch API + LLM extraction
# ---------------------------------------------------------------------------

# LLM schema for extracting structured evidence from raw page text
_FETCH_EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "issue": {
            "type": "object",
            "properties": {
                "url":                   {"type": "string"},
                "title":                 {"type": "string",  "nullable": True},
                "state":                 {"type": "string",  "nullable": True},
                "labels":                {"type": "array",   "items": {"type": "string"}},
                "opened":                {"type": "string",  "nullable": True},
                "author_language_notes": {"type": "string",  "nullable": True},
            },
            "required": ["url"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim":      {"type": "string"},
                    "source_url": {"type": "string", "nullable": True},
                    "kind": {
                        "type": "string",
                        "enum": ["requirement", "blocker", "prerequisite",
                                 "status", "activity", "related_pr"],
                    },
                },
                "required": ["claim", "kind"],
            },
        },
        "related_prs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url":   {"type": "string"},
                    "state": {"type": "string",
                              "enum": ["open", "merged", "closed", "unknown"]},
                    "note":  {"type": "string", "nullable": True},
                },
                "required": ["url", "state"],
            },
        },
        "claim_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "who":        {"type": "string", "nullable": True},
                    "when":       {"type": "string", "nullable": True},
                    "source_url": {"type": "string", "nullable": True},
                },
            },
        },
        "pr_urls_to_fetch": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["issue", "findings"],
}

_FETCH_SYSTEM = """You are extracting structured evidence from raw GitHub page text.

Rules:
- source_url for every finding MUST be the exact URL of the page it came from.
- Only output findings that are present in the provided text.
- In pr_urls_to_fetch, list any GitHub PR URLs mentioned in the issue that you have NOT
  already seen the full text for (max 5).
- state = closed if the issue was marked closed; open otherwise.
- kind choices: requirement | blocker | prerequisite | status | activity | related_pr
- author_language_notes: note if the issue body or comments are in a non-English language.
- Do NOT invent or hallucinate any URL, finding, or PR.
"""


def _build_fetch_prompt(pages: list[FetchPageResult], issue_url: str) -> str:
    blocks: list[str] = []
    for p in pages:
        if p.text:
            # Trim to 6000 chars per page to keep the LLM prompt manageable
            blocks.append(f"=== PAGE: {p.url} ===\n{p.text[:6000]}")
        else:
            blocks.append(f"=== PAGE: {p.url} === [fetch failed: {p.error}]")
    return (
        f"Extract structured evidence from the following GitHub pages.\n"
        f"Issue URL: {issue_url}\n\n"
        + "\n\n".join(blocks)
        + "\n\nReturn a single JSON object matching the schema."
    )


_FAST_PATH_MIN_FINDINGS = 3   # If fetch+LLM yields >= this, skip the agent entirely


async def _fast_fetch_extract(
    issue_url: str,
    on_event: "Callable[[str], Any] | None",
) -> tuple[dict | None, list[str], float]:
    """
    Fetch the issue page (and any linked PR pages) with the Fetch API, then
    ask the LLM to extract structured evidence.

    Returns:
        (raw_dict_or_None, fetched_urls, elapsed_s)
        raw_dict is None on any failure.
    """
    import asyncio
    import time
    from app.llm import generate_json

    t0 = time.monotonic()
    await _emit(on_event, "Reading the issue")

    # Fetch the issue page first
    pages = await fetch_pages(
        [issue_url],
        intent="Extract GitHub issue details: title, state, labels, body, comments, linked PRs, claims",
        timeout_s=20,
    )
    fetched_urls = [issue_url]
    elapsed = time.monotonic() - t0

    if not pages or not pages[0].text:
        logger.warning("Fetch fast path: issue page fetch failed (%s)", pages[0].error if pages else "no result")
        return None, fetched_urls, elapsed

    # Quick scan for PR URLs in the issue text so we can fetch them in parallel
    pr_urls = _extract_pr_urls_from_text(pages[0].text, issue_url)[:4]

    if pr_urls:
        await _emit(on_event, "Checking linked PRs and claims")
        pr_pages = await fetch_pages(
            pr_urls,
            intent="Extract PR state (open/merged/closed), title, and whether it fixes the linked issue",
            timeout_s=20,
        )
        pages.extend(pr_pages)
        fetched_urls.extend(pr_urls)

    # LLM extraction
    prompt = _build_fetch_prompt(pages, issue_url)
    try:
        raw = await generate_json(prompt, _FETCH_EXTRACT_SCHEMA, system=_FETCH_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fetch fast path LLM extraction failed: %s", exc)
        return None, fetched_urls, time.monotonic() - t0

    # If LLM found more PR URLs to fetch, do a second round
    extra_pr_urls = [
        u for u in (raw.get("pr_urls_to_fetch") or [])
        if isinstance(u, str) and u.startswith("https://github.com") and u not in fetched_urls
    ][:3]
    if extra_pr_urls:
        extra_pages = await fetch_pages(
            extra_pr_urls,
            intent="Extract PR state (open/merged/closed) and whether it fixes the linked issue",
            timeout_s=15,
        )
        # Re-run extraction with the new pages appended
        pages.extend(extra_pages)
        fetched_urls.extend(extra_pr_urls)
        prompt2 = _build_fetch_prompt(pages, issue_url)
        try:
            raw = await generate_json(prompt2, _FETCH_EXTRACT_SCHEMA, system=_FETCH_SYSTEM)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Fetch fast path second LLM pass failed: %s", exc)
            # keep the first raw result

    elapsed = time.monotonic() - t0
    logger.info("Fast fetch+extract: %d pages, %.1fs", len(pages), elapsed)
    return raw, fetched_urls, elapsed


def _extract_pr_urls_from_text(text: str, issue_url: str) -> list[str]:
    """Find GitHub PR URLs in raw page text."""
    repo = _repo_slug(issue_url)
    pattern = re.compile(
        r"https://github\.com/" + re.escape(repo) + r"/pull/\d+",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    result: list[str] = []
    for m in pattern.finditer(text):
        u = m.group(0).rstrip("/.,)>\"'")
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------

_STAGES = [
    "Reading the issue",
    "Checking linked PRs and claims",
    "Checking repo activity",
    "Matching against your profile",
    "Writing the report",
]

_STAGE_KEYWORDS: list[tuple[str, ...]] = [
    ("reading", "issue", "title", "label", "open", "description", "body"),
    ("pull request", "pr", "claim", "assignee", "linked", "merged", "fix"),
    ("activity", "commit", "maintainer", "repo", "recent", "contributor", "pulse"),
    ("match", "profile", "skill", "prerequisite", "requirement", "scope", "hours"),
    ("report", "writing", "summary", "structur", "output", "result"),
]


async def _emit(on_event: "Callable[[str], Any] | None", msg: str) -> None:
    import asyncio
    if on_event:
        result = on_event(msg)
        if asyncio.iscoroutine(result):
            await result


def _make_stage_emitter(on_event: "Callable[[str], Any] | None"):
    import asyncio
    next_stage_idx = [0]

    async def emit_stage(raw: str) -> None:
        logger.debug("agent step: %s", raw)
        if on_event is None or next_stage_idx[0] >= len(_STAGES):
            return
        lower = raw.lower()
        for i in range(next_stage_idx[0], len(_STAGES)):
            keywords = _STAGE_KEYWORDS[i]
            if any(kw in lower for kw in keywords):
                stage_label = _STAGES[i]
                next_stage_idx[0] = i + 1
                result = on_event(stage_label)
                if asyncio.iscoroutine(result):
                    await result
                break

    return emit_stage


# ---------------------------------------------------------------------------
# Bundle merging (fast-path + agent-path results)
# ---------------------------------------------------------------------------

def _merge_bundles(base: EvidenceBundle, extra: EvidenceBundle) -> EvidenceBundle:
    """Merge agent-path findings into a fast-path bundle, deduplicating by claim text."""
    seen_claims = {f.claim for f in base.findings}
    for f in extra.findings:
        if f.claim not in seen_claims:
            base.findings.append(f)
            seen_claims.add(f.claim)

    seen_pr_urls = {pr.url for pr in base.related_prs}
    for pr in extra.related_prs:
        if pr.url not in seen_pr_urls:
            base.related_prs.append(pr)
            seen_pr_urls.add(pr.url)

    seen_signals = {s.signal for s in base.activity_signals}
    for s in extra.activity_signals:
        if s.signal not in seen_signals:
            base.activity_signals.append(s)
            seen_signals.add(s.signal)

    # Prefer already_resolved=True from either side
    if extra.already_resolved and not base.already_resolved:
        base.already_resolved = extra.already_resolved
        base.already_resolved_url = extra.already_resolved_url

    base.claim_signals.extend(extra.claim_signals)
    base.unknowns.extend(u for u in extra.unknowns if u not in base.unknowns)
    return base


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def investigate_issue(
    issue_url: str,
    on_event: "Callable[[str], Coroutine | None] | None" = None,
) -> EvidenceBundle:
    """
    Investigate a single GitHub issue and return an EvidenceBundle.

    Pipeline:
    1. Fast path: fetch issue + PR pages, LLM-extract findings (~5–15 s).
       If ≥3 sourced findings → return immediately (path_used="fetch").
    2. If fast path insufficient: run full TinyFish agent with a narrowed goal
       (path_used="fetch+agent" if fast path got anything, else "agent").

    Never raises.  Empty bundle (0 findings) is returned as-is — callers must
    gate on findings count before building a report.
    """
    import asyncio
    import time

    from app.config import get_settings

    t0 = time.monotonic()
    settings = get_settings()
    investigate_timeout = settings.investigate_timeout_s

    # ------------------------------------------------------------------
    # Fast path
    # ------------------------------------------------------------------
    fast_raw, fetched_urls, fast_elapsed = await _fast_fetch_extract(issue_url, on_event)

    fast_bundle: EvidenceBundle | None = None
    if fast_raw and isinstance(fast_raw, dict):
        try:
            fast_bundle, warnings = _validate_and_clean(fast_raw, issue_url)
            fast_bundle.elapsed_s = fast_elapsed
            fast_bundle.path_used = "fetch"
            if warnings:
                logger.info("Fast path: %d finding(s) moved to unknowns", len(warnings))
        except Exception as exc:
            logger.warning("Fast path validation error: %s", exc)
            fast_bundle = None

    fast_finding_count = len(fast_bundle.findings) if fast_bundle else 0
    logger.info(
        "Fast path complete: findings=%d elapsed=%.1fs urls=%s",
        fast_finding_count, fast_elapsed, fetched_urls,
    )

    if fast_finding_count >= _FAST_PATH_MIN_FINDINGS:
        logger.info("Fast path sufficient (%d findings) — skipping agent", fast_finding_count)
        return fast_bundle  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Agent path
    # ------------------------------------------------------------------
    remaining_budget = max(30, investigate_timeout - int(time.monotonic() - t0))
    agent_goal = _AGENT_GOAL_NARROW if fast_finding_count > 0 else _AGENT_GOAL_FULL

    await _emit(on_event, "Checking repo activity")
    stage_emit = _make_stage_emitter(on_event)

    tf_result = await run_tinyfish(
        goal=agent_goal,
        url=issue_url,
        output_schema=_OUTPUT_SCHEMA,
        on_event=stage_emit,
        timeout_s=min(remaining_budget, 120),  # hard cap agent at 120s
    )

    agent_elapsed = time.monotonic() - t0

    # --- agent failed or returned nothing ---
    if not tf_result.ok or tf_result.parsed_result is None:
        error_msg = tf_result.error or "TinyFish returned no result"
        logger.warning("Agent path failed for %s: %s", issue_url, error_msg)

        if fast_bundle and fast_finding_count > 0:
            # We have partial fast-path data; mark it partial
            fast_bundle.partial = True
            fast_bundle.unknowns.append(
                "Investigation was cut short; some context may be missing"
            )
            fast_bundle.elapsed_s = agent_elapsed
            fast_bundle.path_used = "fetch+agent"
            return fast_bundle

        # Truly empty — return empty bundle; caller gates on this
        return EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Agent run failed or returned no data: {error_msg}"],
            partial=True,
            elapsed_s=agent_elapsed,
            path_used="agent",
        )

    raw = tf_result.parsed_result
    if not isinstance(raw, dict):
        empty = EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Unexpected result type from agent: {type(raw).__name__}"],
            partial=True,
            elapsed_s=agent_elapsed,
            path_used="agent",
        )
        if fast_bundle and fast_finding_count > 0:
            return fast_bundle  # prefer partial fast data over nothing
        return empty

    try:
        agent_bundle, warnings = _validate_and_clean(raw, issue_url)
    except Exception as exc:
        logger.exception("Agent validation error for %s", issue_url)
        if fast_bundle and fast_finding_count > 0:
            fast_bundle.partial = True
            fast_bundle.unknowns.append("Investigation was cut short; some context may be missing")
            fast_bundle.elapsed_s = agent_elapsed
            return fast_bundle
        return EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Validation error: {exc}"],
            partial=True,
            elapsed_s=agent_elapsed,
            path_used="agent",
        )

    agent_bundle.elapsed_s = agent_elapsed

    # Merge fast-path findings into agent result (agent usually has more)
    if fast_bundle and fast_finding_count > 0:
        agent_bundle = _merge_bundles(agent_bundle, fast_bundle)
        agent_bundle.path_used = "fetch+agent"
    else:
        agent_bundle.path_used = "agent"

    if warnings:
        logger.info("%d finding(s) moved to unknowns during agent validation", len(warnings))

    # Mark partial if agent timed out with something (tf_result is ok but elapsed near limit)
    if tf_result.elapsed_s >= min(remaining_budget, 120) - 5:
        if agent_bundle.findings:
            agent_bundle.partial = True
            if "Investigation was cut short; some context may be missing" not in agent_bundle.unknowns:
                agent_bundle.unknowns.append(
                    "Investigation was cut short; some context may be missing"
                )

    return agent_bundle
