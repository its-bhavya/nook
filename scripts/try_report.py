"""
Build investigation reports for the same issue with two different personas and
print both so the difference is visible.

Persona A: Python / FastAPI / RAG / DSPy, speaks English and Hindi, has Gemini key.
Persona B: React / TypeScript developer, no LLM API keys, English only.

Usage (from repo root, LLM_API_KEY must be set):
    python scripts/try_report.py

The script uses the mock EvidenceBundle from mock_data.py so it does not
require a TinyFish run.  Set DEMO_MODE=false in .env to run against live data.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.investigate import (
    ActivitySignal,
    ClaimSignal,
    EvidenceBundle,
    Finding,
    IssueHeader,
    RelatedPR,
)
from app.models import Environment, Profile, Skill, SkillLevel
from app.report import build_report

# ---------------------------------------------------------------------------
# Shared evidence bundle (based on the DSPy #9074 mock investigation)
# ---------------------------------------------------------------------------

BUNDLE = EvidenceBundle(
    issue=IssueHeader(
        url="https://github.com/stanfordnlp/dspy/issues/9074",
        title="Bug: LM retry logic ignores backoff parameter in certain providers",
        state="open",
        labels=["bug", "good first issue"],
        opened="2026-10-01",
        author_language_notes=None,
    ),
    findings=[
        Finding(
            claim="PR #9041 'Fix: respect backoff_factor in all provider retry paths' was merged on 2026-09-30.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9041",
            kind="status",
        ),
        Finding(
            claim="Maintainer @okhat commented: 'This looks like a duplicate of #9041 — closing once CI passes.'",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074#issuecomment-2391045",
            kind="status",
        ),
        Finding(
            claim="The issue is still marked 'Open' on GitHub as of the investigation date.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074",
            kind="status",
        ),
        Finding(
            claim="Last commit to dspy/predict/retry.py was 6 days ago, touching the exact lines in scope.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main/dspy/predict/retry.py",
            kind="activity",
        ),
        Finding(
            claim="The retry module lives in dspy/predict/retry.py and uses provider-specific backoff configs.",
            source_url="https://github.com/stanfordnlp/dspy/blob/main/dspy/predict/retry.py",
            kind="requirement",
        ),
        Finding(
            claim="DSPy requires Python 3.10+ and contributors should run the test suite via pytest.",
            source_url="https://github.com/stanfordnlp/dspy/blob/main/CONTRIBUTING.md",
            kind="prerequisite",
        ),
    ],
    related_prs=[
        RelatedPR(
            url="https://github.com/stanfordnlp/dspy/pull/9041",
            state="merged",
            note="Fixes the same backoff bug — makes this issue a duplicate.",
        ),
    ],
    claim_signals=[],
    activity_signals=[
        ActivitySignal(
            signal="DSPy had 12 commits to main in the last 7 days — actively maintained.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main",
        ),
        ActivitySignal(
            signal="Maintainer @okhat replied within 48 hours of the issue being filed.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074#issuecomment-2391045",
        ),
    ],
    unknowns=[
        "Whether the maintainer has already closed the issue after this investigation ran.",
        "Whether a second, related retry bug exists that was not covered by PR #9041.",
    ],
    already_resolved=True,
    already_resolved_url="https://github.com/stanfordnlp/dspy/pull/9041",
    partial=False,
    elapsed_s=0.0,
)

# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------

PERSONA_A = Profile(
    skills=[
        Skill(name="Python", level=SkillLevel.experienced, evidence="5+ years, primary language"),
        Skill(name="FastAPI", level=SkillLevel.experienced, evidence="Built production RAG service"),
        Skill(name="RAG", level=SkillLevel.experienced, evidence="ChromaDB + LangChain project"),
        Skill(name="DSPy", level=SkillLevel.familiar, evidence="Used in evaluation pipeline"),
    ],
    interests=["LLM agents", "RAG", "evaluation frameworks"],
    goals="Find a small first contribution that teaches me how a real agent framework is structured.",
    hours_per_week=6,
    spoken_languages=["English", "Hindi"],
    environment=Environment(has_llm_api_keys=["Gemini"], os="macOS"),
)

PERSONA_B = Profile(
    skills=[
        Skill(name="React", level=SkillLevel.experienced, evidence="3 years building production UIs"),
        Skill(name="TypeScript", level=SkillLevel.experienced, evidence="Primary language for all frontend work"),
        Skill(name="JavaScript", level=SkillLevel.experienced, evidence="Used daily alongside TypeScript"),
    ],
    interests=["frontend tooling", "developer experience", "component libraries"],
    goals="Contribute to open-source projects I actually use day-to-day.",
    hours_per_week=4,
    spoken_languages=["English"],
    environment=Environment(has_llm_api_keys=[], os="Windows"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _divider(label: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {label}")
    print("=" * width)


def _print_report(report, persona_label: str) -> None:
    _divider(persona_label)
    print(f"\nISSUE : {report.issue_url}")
    print(f"FIT   : {report.fit.level.value.upper()}")
    print(f"\nFit reasoning:\n  {report.fit.reasoning}")

    if report.sources_say:
        print("\nSources say:")
        for s in report.sources_say:
            print(f"  • {s.claim}")
            print(f"    → {s.source_url}")

    if report.interpretation:
        print("\nInterpretation:")
        for line in report.interpretation:
            print(f"  • {line}")

    if report.prerequisites:
        print("\nPrerequisites:")
        for p in report.prerequisites:
            print(f"  • {p}")

    if report.blockers:
        print("\nBlockers:")
        for b in report.blockers:
            src = f"  → {b.source_url}" if b.source_url else ""
            print(f"  ✗ {b.text}{src}")

    if report.first_steps:
        print("\nFirst steps:")
        for i, step in enumerate(report.first_steps, 1):
            print(f"  {i}. {step}")

    if report.unknowns:
        print("\nUnknowns:")
        for u in report.unknowns:
            print(f"  ? {u}")

    if report.activity_signals:
        print("\nActivity signals:")
        for sig in report.activity_signals:
            print(f"  ~ {sig.signal}  ({sig.source_url})")

    if report.related_prs:
        print("\nRelated PRs:")
        for pr in report.related_prs:
            print(f"  [{pr.state.value}] {pr.url}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("Building reports for DSPy #9074 with two personas...")
    print("(Both use the same EvidenceBundle — only the profile changes)\n")

    print("Building Persona A report...")
    report_a = await build_report(PERSONA_A, BUNDLE)
    print("Done. Building Persona B report...")
    report_b = await build_report(PERSONA_B, BUNDLE)
    print("Done.\n")

    _print_report(report_a, "PERSONA A — Python / FastAPI / DSPy / Gemini key / English + Hindi")
    _print_report(report_b, "PERSONA B — React / TypeScript / no LLM keys / English only")

    _divider("DIFF HIGHLIGHTS")
    print(f"\n  Fit A : {report_a.fit.level.value}   |   Fit B : {report_b.fit.level.value}")
    print(f"  Blockers A : {len(report_a.blockers)}   |   Blockers B : {len(report_b.blockers)}")
    print(f"  First steps A : {len(report_a.first_steps)}   |   First steps B : {len(report_b.first_steps)}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
