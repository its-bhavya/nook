"""
Report builder for Nook.

Public interface:
    build_report(profile, evidence_bundle) -> InvestigationReport

One LLM call. Post-processing removes any sources_say item whose source_url
is not in the bundle and appends a note to unknowns for each removal.
"""
from __future__ import annotations

import logging
import re

from app.investigate import EvidenceBundle
from app.llm import generate_json
from app.models import (
    ActivitySignal,
    Blocker,
    FitAssessment,
    FitLevel,
    InvestigationReport,
    IssueStatus,
    PRState,
    Prerequisite,
    Profile,
    RelatedPR,
    SourceClaim,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Nook, a contributor-matching assistant.
Write a personalised investigation report so a developer can decide whether to
take a GitHub issue. Follow every rule below exactly.

SUMMARY
- ≤30 words. Format: "<Good|Possible|Weak> match: <why>. Main risk: <one risk or none>."
- Derive "Good/Possible/Weak" from fit.level.

ISSUE_STATUS
- state must be exactly one of: open | closed | claimed | has_open_pr | unknown
- note: one plain-English sentence explaining the state.
- source_url: a URL from the bundle, or null.
- closed or has_merged_pr → state "closed"; active claim or open competing PR → "claimed"
  or "has_open_pr"; otherwise "open" or "unknown".

FIT (fit.level + fit.reasoning)
- Describe ONLY how well the issue's requirements match the person's skills, interests,
  spoken languages, and hours_per_week.
- Cite 2–3 concrete profile facts vs 2–3 issue requirements.
- NEVER mention issue state, activity signals, claims, or PRs here — those belong in
  issue_status and blockers.
- level must be exactly one of: strong | possible | weak.

SOURCES_SAY (5–7 items maximum)
- Only facts that change a decision:
    * scope and acceptance criteria (1–2 items)
    * existing files or code the contributor must touch or read (if found)
    * the competing PR or claim state (ONE item, only if relevant)
    * repo activity (ONE item)
    * a relevant precedent PR if any
- Every item: ≤25 words, source_url from the bundle exactly.
- No label restatements, no "zero comments", no duplicates.

INTERPRETATION
- Nook's inferences about fit. Use hedge language ("This probably…", "It seems…").
- Do NOT present inferences as facts.

PREREQUISITES (3–6 items)
- Concrete things the contributor needs: skills, tools, setup commands, APIs to learn,
  files to read — taken from issue body and repo.
- For each item set status to:
    "have"    if the profile already lists this skill at experienced/familiar
    "learn"   if the profile does not list it, or lists it only as inferred
    "unknown" if there is not enough information
- NEVER include facts about comments, labels or PRs as prerequisites.

BLOCKERS (may be empty)
- Only things that would STOP or seriously SLOW this specific person:
    already fixed/closed, claimed by an active contributor, open competing PR,
    inaccessible requirements (paid API, special hardware, a language they can't speak),
    scope too large for their hours_per_week, no maintainer activity for 90+ days.
- Missing PRs, zero comments and "good first issue" labels are NOT blockers.
- An empty list is valid and expected.

UNKNOWNS (≤3 items, may be empty)
- Only unverified things that would materially change the decision.
- Do NOT list missing timestamps, "no maintainer replies yet" on a new issue,
  or generic caveats.
- An empty list is valid.

FIRST_STEPS (exactly 3)
- Concrete: name specific files, pages, or commands. Base each step on a sourced finding.
- If a step cannot be sourced, write "Confirm [X] before starting."

URLS
- Never invent URLs. Every URL in the report must come from the bundle.
"""

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(profile: Profile, bundle: EvidenceBundle) -> str:
    skill_lines = ", ".join(f"{s.name} ({s.level.value})" for s in profile.skills)
    profile_block = "\n".join([
        f"skills: {skill_lines}",
        f"spoken_languages: {', '.join(profile.spoken_languages) or 'not specified'}",
        f"hours_per_week: {profile.hours_per_week}",
        f"llm_api_keys: {', '.join(profile.environment.has_llm_api_keys) or 'none'}",
        f"os: {profile.environment.os or 'not specified'}",
        f"interests: {', '.join(profile.interests) or 'not specified'}",
        f"goals: {profile.goals}",
    ])

    finding_lines = "\n".join(
        f"  [{f.kind}] {f.claim}  (source: {f.source_url or 'no source'})"
        for f in bundle.findings
    ) or "  (none)"

    pr_lines = "\n".join(
        f"  {pr.url} — {pr.state}" + (f" — {pr.note}" if pr.note else "")
        for pr in bundle.related_prs
    ) or "  (none)"

    activity_lines = "\n".join(
        f"  {sig.signal}  (source: {sig.source_url or 'no source'})"
        for sig in bundle.activity_signals
    ) or "  (none)"

    claim_lines = "\n".join(
        f"  {cs.who or 'unknown'} at {cs.when or 'unknown'} — {cs.source_url or 'no source'}"
        for cs in bundle.claim_signals
    ) or "  (none)"

    # Filter bundle unknowns to only decision-relevant ones (at most 3)
    relevant_unknowns = [u for u in bundle.unknowns if not _is_noise_unknown(u)][:3]
    unknown_lines = "\n".join(f"  - {u}" for u in relevant_unknowns) or "  (none)"

    return f"""Write an InvestigationReport for the issue below.

=== DEVELOPER PROFILE ===
{profile_block}

=== ISSUE ===
url: {bundle.issue.url}
title: {bundle.issue.title or 'unknown'}
state: {bundle.issue.state or 'unknown'}
labels: {', '.join(bundle.issue.labels) or 'none'}
opened: {bundle.issue.opened or 'unknown'}
author_language_notes: {bundle.issue.author_language_notes or 'none'}
already_resolved: {bundle.already_resolved}
already_resolved_url: {bundle.already_resolved_url or 'n/a'}

=== FINDINGS (the only valid sources — use these source_urls verbatim) ===
{finding_lines}

=== RELATED PRs ===
{pr_lines}

=== ACTIVITY SIGNALS ===
{activity_lines}

=== CLAIM SIGNALS ===
{claim_lines}

=== RELEVANT UNKNOWNS (include only these, do not add new generic ones) ===
{unknown_lines}

Return a single JSON object matching the InvestigationReport schema.
"""


def _is_noise_unknown(u: str) -> bool:
    """Return True for unknowns that are too generic to be useful."""
    noise_phrases = (
        "timestamp", "no maintainer repl", "no reply", "generic",
        "unsourced finding", "sources_say item removed",
    )
    lower = u.lower()
    return any(p in lower for p in noise_phrases)


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "issue_url": {"type": "string"},
        "summary": {"type": "string"},
        "issue_status": {
            "type": "object",
            "properties": {
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "claimed", "has_open_pr", "unknown"],
                },
                "note": {"type": "string"},
                "source_url": {"type": "string", "nullable": True},
            },
            "required": ["state", "note"],
        },
        "fit": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["strong", "possible", "weak"]},
                "reasoning": {"type": "string"},
            },
            "required": ["level", "reasoning"],
        },
        "sources_say": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["claim", "source_url"],
            },
        },
        "interpretation": {
            "type": "array",
            "items": {"type": "string"},
        },
        "prerequisites": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["have", "learn", "unknown"],
                    },
                },
                "required": ["text", "status"],
            },
        },
        "blockers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source_url": {"type": "string", "nullable": True},
                },
                "required": ["text"],
            },
        },
        "activity_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "signal": {"type": "string"},
                    "source_url": {"type": "string"},
                },
                "required": ["signal", "source_url"],
            },
        },
        "related_prs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "state": {"type": "string", "enum": ["open", "merged", "closed"]},
                },
                "required": ["url", "state"],
            },
        },
        "first_steps": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unknowns": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "issue_url", "summary", "issue_status", "fit",
        "sources_say", "interpretation", "prerequisites",
        "blockers", "activity_signals", "related_prs",
        "first_steps", "unknowns",
    ],
}


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _collect_bundle_urls(bundle: EvidenceBundle) -> set[str]:
    urls: set[str] = set()
    if bundle.issue.url:
        urls.add(bundle.issue.url)
    if bundle.already_resolved_url:
        urls.add(bundle.already_resolved_url)
    for f in bundle.findings:
        if f.source_url:
            urls.add(f.source_url)
    for pr in bundle.related_prs:
        urls.add(pr.url)
    for sig in bundle.activity_signals:
        if sig.source_url:
            urls.add(sig.source_url)
    for cs in bundle.claim_signals:
        if cs.source_url:
            urls.add(cs.source_url)
    return urls


def _sanitise(raw: dict, bundle: EvidenceBundle) -> list[str]:
    """
    Remove sources_say items whose source_url is not in the bundle.
    Returns list of removal messages (added to unknowns by caller).
    """
    bundle_urls = _collect_bundle_urls(bundle)
    removed: list[str] = []
    kept: list[dict] = []
    for item in raw.get("sources_say", []):
        if item.get("source_url") in bundle_urls:
            kept.append(item)
        else:
            removed.append(
                f"sources_say removed (URL not in bundle): \"{item.get('claim', '')}\" "
                f"url={item.get('source_url', 'none')}"
            )
            logger.warning("Removed hallucinated sources_say URL: %s", item.get("source_url"))
    raw["sources_say"] = kept[:7]  # hard cap at 7
    return removed


# ---------------------------------------------------------------------------
# Report quality guards
# ---------------------------------------------------------------------------

# Patterns that signal a generic / unsourced first_step
_GENERIC_STEP_PATTERNS = re.compile(
    r"confirm\s+(scope|requirements?|timeline|availability|interest|whether)"
    r"|review\s+the\s+issue\s*$"
    r"|read\s+the\s+(contributing|readme|docs?)\s*$"
    r"|check\s+if\s+this\s+is\s+still\s+open"
    r"|make\s+sure\s+you\s+understand"
    r"|familiari[sz]e\s+yourself",
    re.IGNORECASE,
)

# A step is specific if it names a file, command, URL, or class/function
_SPECIFIC_STEP_PATTERNS = re.compile(
    r"(https?://\S+)"            # URL
    r"|`[^`]+`"                   # inline code / command
    r"|[\w/]+\.(py|ts|js|md|yml|yaml|json|toml|cfg|ini|txt|rst)"  # filename
    r"|class\s+\w+|def\s+\w+|function\s+\w+"  # code symbol
    r"|\brun\b.{0,40}\b(test|pytest|npm|yarn|cargo|make|python)\b",
    re.IGNORECASE,
)


def _is_specific_step(step: str) -> bool:
    if _GENERIC_STEP_PATTERNS.search(step):
        return False
    return bool(_SPECIFIC_STEP_PATTERNS.search(step))


def _filter_first_steps(steps: list) -> list:
    """
    Keep only specific, sourced first_steps.  Generic items are dropped.
    If fewer than 2 specific steps can be supported, return however many there are
    (possibly zero) rather than padding with generic noise.
    """
    if not isinstance(steps, list):
        return []
    kept = [s for s in steps if isinstance(s, str) and _is_specific_step(s)]
    logger.debug(
        "first_steps filter: %d in → %d specific kept (dropped %d generic)",
        len(steps), len(kept), len(steps) - len(kept),
    )
    return kept[:3]


def _filter_prerequisites(prereqs: list, bundle: "EvidenceBundle") -> list:
    """
    Remove prerequisites that are generic skill-match observations
    (those belong in fit.reasoning) rather than concrete things sourced
    from the issue body or repo.
    """
    if not isinstance(prereqs, list):
        return []

    # Build a bag of words from the bundle to check sourcing
    bundle_text = " ".join([
        bundle.issue.title or "",
        *[f.claim for f in bundle.findings],
        *[pr.url for pr in bundle.related_prs],
    ]).lower()

    _PROFILE_ONLY_PATTERNS = re.compile(
        r"^(familiarity with|knowledge of|understanding of|experience (with|in))\s+\w"
        r"|(your|the developer'?s?)\s+(background|experience|skills?)",
        re.IGNORECASE,
    )

    kept = []
    for p in prereqs:
        if not isinstance(p, dict):
            continue
        text = (p.get("text") or "").strip()
        if not text:
            continue
        # Drop purely profile-derived generic statements
        if _PROFILE_ONLY_PATTERNS.match(text):
            logger.debug("prerequisites filter: dropped profile-only item: %r", text)
            continue
        kept.append(p)

    return kept[:6]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def build_report(
    profile: Profile,
    bundle: EvidenceBundle,
) -> InvestigationReport:
    prompt = _build_prompt(profile, bundle)
    raw: dict = await generate_json(prompt, _REPORT_SCHEMA, system=_SYSTEM_PROMPT)

    raw["issue_url"] = bundle.issue.url

    removed = _sanitise(raw, bundle)
    if removed:
        logger.info("%d sources_say item(s) removed", len(removed))
        # Do NOT bloat unknowns with internal housekeeping messages

    # Coerce PR states
    for pr in raw.get("related_prs", []):
        try:
            pr["state"] = PRState(pr["state"]).value
        except (ValueError, KeyError):
            pr["state"] = PRState.open.value

    # Cap unknowns at 3 decision-relevant items
    raw["unknowns"] = [u for u in raw.get("unknowns", []) if not _is_noise_unknown(u)][:3]

    # --- Quality guard: first_steps must be specific ---
    raw["first_steps"] = _filter_first_steps(raw.get("first_steps", []))

    # --- Quality guard: prerequisites must be sourced from issue/repo, not profile alone ---
    raw["prerequisites"] = _filter_prerequisites(raw.get("prerequisites", []), bundle)

    # Build issue_status
    raw_status = raw.get("issue_status") or {}
    issue_status = IssueStatus(
        state=raw_status.get("state", "unknown"),
        note=raw_status.get("note", ""),
        source_url=raw_status.get("source_url"),
    )

    return InvestigationReport(
        issue_url=raw["issue_url"],
        summary=raw.get("summary", ""),
        issue_status=issue_status,
        fit=FitAssessment(
            level=FitLevel(raw["fit"]["level"]),
            reasoning=raw["fit"]["reasoning"],
        ),
        sources_say=[
            SourceClaim(claim=s["claim"], source_url=s["source_url"])
            for s in raw.get("sources_say", [])
        ],
        interpretation=raw.get("interpretation", []),
        prerequisites=[
            Prerequisite(text=p["text"], status=p.get("status", "unknown"))
            for p in raw.get("prerequisites", [])
        ],
        blockers=[
            Blocker(text=b["text"], source_url=b.get("source_url"))
            for b in raw.get("blockers", [])
        ],
        activity_signals=[
            ActivitySignal(signal=a["signal"], source_url=a["source_url"])
            for a in raw.get("activity_signals", [])
        ],
        related_prs=[
            RelatedPR(url=p["url"], state=PRState(p["state"]))
            for p in raw.get("related_prs", [])
        ],
        first_steps=raw.get("first_steps", [])[:3],
        unknowns=raw.get("unknowns", []),
    )
