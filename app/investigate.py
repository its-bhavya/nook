"""
Investigation service for Nook.

Public entry point:
    investigate_issue(issue_url, on_event) -> EvidenceBundle

Calls run_tinyfish once, validates the result, and returns a structured
EvidenceBundle. Never raises — partial or failed results come back with
partial=True and a populated unknowns list.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Coroutine, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.tinyfish import run_tinyfish

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
    already_resolved_url: Optional[str] = None  # supporting URL when already_resolved is True
    partial: bool = False
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# TinyFish goal and output_schema
# ---------------------------------------------------------------------------

_GOAL = (
    "Investigate this GitHub issue. "
    "Identify relevant pull requests (with their state: open, merged, or closed), "
    "discussion that changes the scope or status of the issue, "
    "documentation, prerequisites, possible blockers, "
    "signs that someone has claimed it or that a fix has already merged, "
    "and repository activity signals such as recent maintainer replies and recent commits. "
    "Return supporting source URLs for every finding and explicitly list anything "
    "you cannot verify."
)

# TinyFish output_schema — must follow the strict allowlist rules:
# - top level is object
# - nullable uses nullable:true not type:[...,null]
# - no oneOf/const/$ref
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

# GitHub URLs we accept as source_url values
_GITHUB_PATTERN = re.compile(
    r"^https://(github\.com|raw\.githubusercontent\.com|gist\.github\.com)/",
    re.IGNORECASE,
)

# Documentation/community domains that are valid cross-repo sources
_DOC_DOMAINS = {
    "docs.github.com", "docs.python.org", "pypi.org", "packaging.python.org",
    "readthedocs.io", "readthedocs.org", "rtfd.io",
}

# Pattern for a valid GitHub issue URL: https://github.com/owner/repo/issues/N
_ISSUE_URL_PATTERN = re.compile(
    r"^https://github\.com/[\w.\-]+/[\w.\-]+/issues/\d+",
    re.IGNORECASE,
)


def _repo_slug(issue_url: str) -> str:
    """Extract 'owner/repo' from a GitHub issue URL."""
    parts = urlparse(issue_url).path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return ""


def _is_valid_source(url: Any, repo: str) -> bool:
    """Return True if url is a usable, non-fabricated source URL."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    parsed = urlparse(url)
    domain = parsed.netloc.lower()

    # Known documentation domains are always acceptable
    if any(domain == d or domain.endswith("." + d) for d in _DOC_DOMAINS):
        return True

    # Must be a GitHub URL
    if not _GITHUB_PATTERN.match(url):
        return False

    # If it's a github.com URL, it must belong to the same repo OR be a clearly
    # cross-repo reference (we allow any github.com URL but log cross-repo ones)
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
    """
    Validate the raw TinyFish result dict and return a clean EvidenceBundle
    plus a list of validation warnings (moved-to-unknowns messages).
    """
    warnings: list[str] = []
    repo = _repo_slug(issue_url)

    # --- issue header ---
    raw_issue = raw.get("issue") or {}
    issue = IssueHeader(
        url=raw_issue.get("url") or issue_url,
        title=raw_issue.get("title"),
        state=raw_issue.get("state"),
        labels=raw_issue.get("labels") or [],
        opened=raw_issue.get("opened"),
        author_language_notes=raw_issue.get("author_language_notes"),
    )

    # --- findings: drop unsourced, move to unknowns ---
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
            # No valid source → move to unknowns
            warnings.append(f"Unsourced finding moved to unknowns: \"{claim}\"")

    # --- related PRs: must have valid GitHub URL ---
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

    # --- claim signals ---
    clean_claims: list[ClaimSignal] = []
    for cs in raw.get("claim_signals") or []:
        src = cs.get("source_url")
        clean_claims.append(ClaimSignal(
            who=cs.get("who"),
            when=cs.get("when"),
            source_url=src if _is_valid_source(src, repo) else None,
        ))

    # --- activity signals ---
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

    # --- agent-reported unknowns ---
    unknowns: list[str] = list(raw.get("unknowns") or [])
    unknowns.extend(warnings)

    # --- already_resolved check ---
    already_resolved = False
    resolved_url: str | None = None

    issue_state_closed = (issue.state or "").lower() == "closed"

    # Check related PRs for a merged one
    merged_pr = next((pr for pr in clean_prs if pr.state == "merged"), None)

    # Also scan findings for status-kind entries mentioning "fix" + "merged"
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
# Public entry point
# ---------------------------------------------------------------------------

async def investigate_issue(
    issue_url: str,
    on_event: Callable[[str], Coroutine | None] | None = None,
) -> EvidenceBundle:
    """
    Investigate a single GitHub issue via TinyFish and return an EvidenceBundle.

    Never raises. On failure or partial result, returns a bundle with
    partial=True and a populated unknowns list.

    Args:
        issue_url: Full GitHub issue URL.
        on_event:  Optional callable forwarded to run_tinyfish for live progress.
    """

    async def _emit(msg: str) -> None:
        logger.info("investigate_issue: %s", msg)
        if on_event:
            result = on_event(msg)
            if hasattr(result, "__await__"):
                await result  # type: ignore[union-attr]

    await _emit(f"Starting investigation: {issue_url}")

    # Wrap on_event to prefix agent messages so callers can distinguish them
    async def _agent_event(msg: str) -> None:
        await _emit(f"Agent: {msg}")

    tf_result = await run_tinyfish(
        goal=_GOAL,
        url=issue_url,
        output_schema=_OUTPUT_SCHEMA,
        on_event=_agent_event,
    )

    await _emit("Validating agent output...")

    # --- failed or empty run ---
    if not tf_result.ok or tf_result.parsed_result is None:
        error_msg = tf_result.error or "TinyFish returned no result"
        logger.warning("Investigation failed for %s: %s", issue_url, error_msg)
        await _emit(f"Investigation incomplete: {error_msg}")
        return EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Agent run failed or returned no data: {error_msg}"],
            partial=True,
            elapsed_s=tf_result.elapsed_s,
        )

    raw = tf_result.parsed_result

    # --- raw result is not a dict (unexpected shape) ---
    if not isinstance(raw, dict):
        await _emit("Unexpected result shape — marking partial")
        return EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Unexpected result type from agent: {type(raw).__name__}"],
            partial=True,
            elapsed_s=tf_result.elapsed_s,
        )

    # --- validate and clean ---
    try:
        bundle, warnings = _validate_and_clean(raw, issue_url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Validation error for %s", issue_url)
        await _emit(f"Validation error — marking partial: {exc}")
        return EvidenceBundle(
            issue=IssueHeader(url=issue_url),
            unknowns=[f"Validation error: {exc}"],
            partial=True,
            elapsed_s=tf_result.elapsed_s,
        )

    bundle.elapsed_s = tf_result.elapsed_s

    if warnings:
        logger.info("%d finding(s) moved to unknowns during validation", len(warnings))

    resolution_note = (
        f" already_resolved=True supporting_url={bundle.already_resolved_url}"
        if bundle.already_resolved else ""
    )
    await _emit(f"Done. findings={len(bundle.findings)} prs={len(bundle.related_prs)}"
                f" unknowns={len(bundle.unknowns)}{resolution_note}")

    return bundle
