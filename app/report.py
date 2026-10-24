"""
Report builder for Nook.

Public interface:
    build_report(profile, evidence_bundle) -> InvestigationReport

One LLM call with a strict output schema.  After the call a post-processing
check removes any sources_say item whose source_url is not in the bundle and
appends a note to unknowns for each item removed.
"""
from __future__ import annotations

import logging

from app.investigate import EvidenceBundle
from app.llm import generate_json
from app.models import (
    ActivitySignal,
    Blocker,
    FitAssessment,
    FitLevel,
    InvestigationReport,
    PRState,
    Profile,
    RelatedPR,
    SourceClaim,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Nook, a contributor-matching assistant.
Your job is to write a personalised investigation report that helps a developer
decide whether to take a GitHub issue.

Rules you must follow exactly:

SOURCES_SAY
- May contain only claims that appear in the evidence_bundle findings.
- Every item must include its source_url exactly as it appears in the bundle.
- Do not add anything else here. Do not invent claims or URLs.

INTERPRETATION
- Holds Nook's inferences about fit, written as inference ("This probably requires…").
- May draw on the developer profile (skills, spoken_languages, hours_per_week,
  environment, interests, goals).
- Do NOT present inferences as facts.

UNKNOWNS
- Must include every item from the bundle's unknowns list (copy them verbatim).
- Also add anything the report itself cannot support from the evidence.

FIT LEVEL
- Must be exactly one of: strong | possible | weak
- Do NOT output a numeric score.
- Reasoning must be plain language that explicitly names the profile facts used
  (e.g. specific skills, spoken_languages, hours_per_week, tools, API keys).

ALREADY RESOLVED
- If the bundle says already_resolved is true, say so first in the fit reasoning.
- Explain what the developer should do instead:
  check whether the fix is complete, close or comment on the issue if appropriate,
  or pick a different issue.

BLOCKERS
- Must be specific to this developer.
- Consider: language of the thread vs spoken_languages in the profile,
  required accounts or services vs environment.has_llm_api_keys / os,
  estimated scope vs hours_per_week.

FIRST_STEPS
- Must be concrete: name files or pages to read, describe how to reproduce the issue.
- Every first step must be based on a sourced finding from the bundle.
- If a step cannot be sourced, say "A first step is to confirm [X]".

URLS
- Never invent URLs. Every URL in the report must come from the bundle.
"""

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(profile: Profile, bundle: EvidenceBundle) -> str:
    profile_lines = [
        f"skills: {', '.join(f'{s.name} ({s.level.value})' for s in profile.skills)}",
        f"spoken_languages: {', '.join(profile.spoken_languages) or 'not specified'}",
        f"hours_per_week: {profile.hours_per_week}",
        f"llm_api_keys: {', '.join(profile.environment.has_llm_api_keys) or 'none'}",
        f"os: {profile.environment.os or 'not specified'}",
        f"interests: {', '.join(profile.interests) or 'not specified'}",
        f"goals: {profile.goals}",
    ]

    finding_lines = []
    for f in bundle.findings:
        src = f.source_url or "no source"
        finding_lines.append(f"  [{f.kind}] {f.claim}  (source: {src})")

    pr_lines = [
        f"  {pr.url} — {pr.state}" + (f" — {pr.note}" if pr.note else "")
        for pr in bundle.related_prs
    ]

    activity_lines = [
        f"  {sig.signal}  (source: {sig.source_url or 'no source'})"
        for sig in bundle.activity_signals
    ]

    claim_lines = [
        f"  {cs.who or 'unknown'} at {cs.when or 'unknown'} — {cs.source_url or 'no source'}"
        for cs in bundle.claim_signals
    ]

    unknown_lines = [f"  - {u}" for u in bundle.unknowns]

    return f"""Write an InvestigationReport for the issue below.

=== DEVELOPER PROFILE ===
{chr(10).join(profile_lines)}

=== ISSUE ===
url: {bundle.issue.url}
title: {bundle.issue.title or 'unknown'}
state: {bundle.issue.state or 'unknown'}
labels: {', '.join(bundle.issue.labels) or 'none'}
opened: {bundle.issue.opened or 'unknown'}
author_language_notes: {bundle.issue.author_language_notes or 'none'}
already_resolved: {bundle.already_resolved}
already_resolved_url: {bundle.already_resolved_url or 'n/a'}

=== FINDINGS (these are the only valid sources) ===
{chr(10).join(finding_lines) or '  (none)'}

=== RELATED PRs ===
{chr(10).join(pr_lines) or '  (none)'}

=== ACTIVITY SIGNALS ===
{chr(10).join(activity_lines) or '  (none)'}

=== CLAIM SIGNALS ===
{chr(10).join(claim_lines) or '  (none)'}

=== BUNDLE UNKNOWNS (copy all of these into the report unknowns) ===
{chr(10).join(unknown_lines) or '  (none)'}

Return a single JSON object matching the InvestigationReport schema.
"""


# ---------------------------------------------------------------------------
# Output schema (mirrors InvestigationReport but as a plain dict for the LLM)
# ---------------------------------------------------------------------------

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "issue_url": {"type": "string"},
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
            "items": {"type": "string"},
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
        "issue_url", "fit", "sources_say", "interpretation",
        "prerequisites", "blockers", "activity_signals",
        "related_prs", "first_steps", "unknowns",
    ],
}


# ---------------------------------------------------------------------------
# Post-processing: remove hallucinated source URLs
# ---------------------------------------------------------------------------

def _collect_bundle_urls(bundle: EvidenceBundle) -> set[str]:
    """Collect every URL that appears anywhere in the bundle."""
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


def _sanitise_report(raw: dict, bundle: EvidenceBundle) -> tuple[dict, list[str]]:
    """
    Remove sources_say items whose source_url is not in the bundle.
    Returns the cleaned dict and a list of removed-item messages.
    """
    bundle_urls = _collect_bundle_urls(bundle)
    removed: list[str] = []
    kept: list[dict] = []

    for item in raw.get("sources_say", []):
        if item.get("source_url") in bundle_urls:
            kept.append(item)
        else:
            removed.append(
                f"sources_say item removed (URL not in bundle): \"{item.get('claim', '')}\" "
                f"(url={item.get('source_url', 'none')})"
            )
            logger.warning("Removed hallucinated sources_say URL: %s", item.get("source_url"))

    raw["sources_say"] = kept

    if removed:
        raw.setdefault("unknowns", []).extend(removed)

    return raw, removed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def build_report(
    profile: Profile,
    bundle: EvidenceBundle,
) -> InvestigationReport:
    """
    Build a personalised InvestigationReport with one LLM call.

    Runs a post-processing pass that removes any sources_say item whose
    source_url is not present in the bundle, and appends a note to unknowns
    for each removal.
    """
    prompt = _build_prompt(profile, bundle)
    raw: dict = await generate_json(prompt, _REPORT_SCHEMA, system=_SYSTEM_PROMPT)

    # Guarantee issue_url matches the bundle
    raw["issue_url"] = bundle.issue.url

    # Remove hallucinated source URLs and note them in unknowns
    raw, removed = _sanitise_report(raw, bundle)
    if removed:
        logger.info("%d sources_say item(s) removed during post-processing", len(removed))

    # Coerce related_prs state to PRState enum values (LLM may return lowercase strings)
    for pr in raw.get("related_prs", []):
        try:
            pr["state"] = PRState(pr["state"]).value
        except (ValueError, KeyError):
            pr["state"] = PRState.open.value

    return InvestigationReport(
        issue_url=raw["issue_url"],
        fit=FitAssessment(
            level=FitLevel(raw["fit"]["level"]),
            reasoning=raw["fit"]["reasoning"],
        ),
        sources_say=[
            SourceClaim(claim=s["claim"], source_url=s["source_url"])
            for s in raw.get("sources_say", [])
        ],
        interpretation=raw.get("interpretation", []),
        prerequisites=raw.get("prerequisites", []),
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
        first_steps=raw.get("first_steps", []),
        unknowns=raw.get("unknowns", []),
    )
