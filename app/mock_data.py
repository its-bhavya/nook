"""
Realistic mock responses used when DEMO_MODE=true or when live services are not yet wired up.
All URLs reference real issues from CONTEXT.md section 14.

MOCK_REPORTS: dict keyed by issue_url for mock investigate runner.
Only one report exists in the mock dataset (DSPy #9074 for Persona A).
The mock runner will fail with a clear error for any URL not in this dict.
"""
from app.models import (
    ActivitySignal,
    Blocker,
    CandidateIssue,
    ClaimantSignal,
    FitAssessment,
    FitLevel,
    InvestigationReport,
    IssueSource,
    IssueStatus,
    PRState,
    Prerequisite,
    Profile,
    RelatedPR,
    Skill,
    SkillLevel,
    SourceClaim,
    Environment,
)

# ---------------------------------------------------------------------------
# Persona A profile (Python / FastAPI / RAG)
# No real personal contact details.
# ---------------------------------------------------------------------------

MOCK_PROFILE = Profile(
    skills=[
        Skill(name="Python", level=SkillLevel.experienced, evidence="5+ years, primary language"),
        Skill(name="FastAPI", level=SkillLevel.experienced, evidence="Built production RAG service"),
        Skill(name="RAG", level=SkillLevel.experienced, evidence="ChromaDB + LangChain project"),
        Skill(name="ChromaDB", level=SkillLevel.experienced, evidence="Vector store in main project"),
        Skill(name="DSPy", level=SkillLevel.familiar, evidence="Used in evaluation pipeline"),
        Skill(name="MLflow", level=SkillLevel.familiar, evidence="Experiment tracking"),
        Skill(name="Hugging Face", level=SkillLevel.familiar, evidence="Fine-tuning transformers"),
        Skill(name="TypeScript", level=SkillLevel.inferred, evidence="Mentioned in resume skills list"),
    ],
    interests=["LLM agents", "RAG", "evaluation frameworks", "open-source tooling"],
    goals="Find a first contribution that is small enough to ship in a weekend but teaches me how a real agent framework is structured",
    hours_per_week=6,
    spoken_languages=["English", "Hindi"],
    environment=Environment(has_llm_api_keys=["Gemini", "OpenAI"], os="macOS"),
)

# ---------------------------------------------------------------------------
# Persona B profile (React / TypeScript / frontend tooling)
# No real personal contact details.
# ---------------------------------------------------------------------------

MOCK_PROFILE_B = Profile(
    skills=[
        Skill(name="React",      level=SkillLevel.experienced, evidence="Built several production SPAs"),
        Skill(name="TypeScript", level=SkillLevel.experienced, evidence="Primary language for all recent projects"),
        Skill(name="CSS",        level=SkillLevel.experienced, evidence="Responsive layouts, design systems"),
        Skill(name="Vite",       level=SkillLevel.familiar,    evidence="Uses Vite for local dev on personal projects"),
        Skill(name="Jest",       level=SkillLevel.familiar,    evidence="Unit tests for React components"),
        Skill(name="Node.js",    level=SkillLevel.familiar,    evidence="Simple Express APIs alongside frontend work"),
        Skill(name="Python",     level=SkillLevel.inferred,    evidence="Occasional scripting, no project experience"),
    ],
    interests=["frontend tooling", "developer experience", "component libraries", "accessibility"],
    goals="Find a small, well-scoped frontend or tooling issue where I can make a real improvement without needing backend knowledge",
    hours_per_week=4,
    spoken_languages=["English"],
    environment=Environment(has_llm_api_keys=[], os="Windows"),
)

# ---------------------------------------------------------------------------
# Mock discovery: 6 candidate issues (mix of traps and good fits)
# ---------------------------------------------------------------------------

MOCK_CANDIDATES: list[CandidateIssue] = [
    CandidateIssue(
        url="https://github.com/stanfordnlp/dspy/issues/9074",
        repo="stanfordnlp/dspy",
        title="Bug: LM retry logic ignores backoff parameter in certain providers",
        labels=["bug", "good first issue"],
        opened="2026-10-01",
        comments=14,
        claimant_signal=ClaimantSignal.none,
        source=IssueSource.github_search,
        theme="LLM agents",
        matched_skills=["Python", "DSPy"],
        reason="Retry logic bug in a framework you already use; small scope, Python only.",
    ),
    CandidateIssue(
        url="https://github.com/pydantic/pydantic-ai/issues/11061",
        repo="pydantic/pydantic-ai",
        title="LLM-API error handling: surface provider error codes to the caller",
        labels=["good first issue", "enhancement"],
        opened="2026-10-15",
        comments=5,
        claimant_signal=ClaimantSignal.none,
        source=IssueSource.github_search,
        theme="LLM agents",
        matched_skills=["Python", "FastAPI"],
        reason="Error-handling addition; your FastAPI background transfers directly.",
    ),
    CandidateIssue(
        url="https://github.com/NVIDIA/garak/issues/2336",
        repo="NVIDIA/garak",
        title="Add missing type annotations to probe base class",
        labels=["good first issue", "typing"],
        opened="2026-10-10",
        comments=2,
        claimant_signal=ClaimantSignal.none,
        source=IssueSource.github_search,
        theme="evaluation frameworks",
        matched_skills=["Python"],
        reason="Pure type-annotation task, no domain knowledge required beyond Python.",
    ),
    CandidateIssue(
        url="https://github.com/supabase-community/supabase-py/issues/1809",
        repo="supabase-community/supabase-py",
        title="Implement missing `.count()` method on query builder",
        labels=["good first issue"],
        opened="2026-09-28",
        comments=3,
        claimant_signal=ClaimantSignal.claimed,
        source=IssueSource.github_search,
        theme="open-source tooling",
        matched_skills=["Python"],
        reason="Small addition to a Python SDK, but someone has already claimed it.",
    ),
    CandidateIssue(
        url="https://github.com/confident-ai/deepeval/issues/3516",
        repo="confident-ai/deepeval",
        title="Add TypeScript SDK support for custom metrics",
        labels=["good first issue", "typescript"],
        opened="2026-10-05",
        comments=7,
        claimant_signal=ClaimantSignal.none,
        source=IssueSource.aggregator,
        theme="evaluation frameworks",
        matched_skills=["TypeScript"],
        reason="Matches your interest in evaluation frameworks; TypeScript listed on your resume.",
    ),
    CandidateIssue(
        url="https://github.com/vectorize-io/hindsight/issues/5981",
        repo="vectorize-io/hindsight",
        title="Add agent memory retrieval step to default pipeline",
        labels=["enhancement", "good first issue"],
        opened="2026-10-18",
        comments=1,
        claimant_signal=ClaimantSignal.linked_pr,
        source=IssueSource.aggregator,
        theme="RAG",
        matched_skills=["Python", "RAG"],
        reason="Directly relevant to RAG experience, but a PR is already linked.",
    ),
]

# ---------------------------------------------------------------------------
# Mock investigation: DSPy #9074 — closed issue trap (Persona A)
# Issue was closed on 2026-08-07. Fix merged in PR #9073. PR #9448 closed as duplicate.
# ---------------------------------------------------------------------------

MOCK_INVESTIGATION = InvestigationReport(
    issue_url="https://github.com/stanfordnlp/dspy/issues/9074",
    summary="Weak match: issue is already closed and fix merged in PR #9073. Main risk: no work remaining.",
    issue_status=IssueStatus(
        state="closed",
        note="Closed on 2026-08-07 when PR #9073 was merged.",
        source_url="https://github.com/stanfordnlp/dspy/issues/9074",
    ),
    fit=FitAssessment(
        level=FitLevel.weak,
        reasoning=(
            "This issue is closed. The underlying bug was fixed in PR #9073, which merged on "
            "2026-08-07 and closed this issue directly. A follow-up PR #9448 was opened by "
            "another contributor after the fix and was closed as a duplicate. "
            "The DSPy codebase is a strong skill match (Python, agent frameworks), "
            "but there is nothing left to contribute here."
        ),
    ),
    sources_say=[
        SourceClaim(
            claim="Issue #9074 is marked Closed on GitHub. Closed on 2026-08-07.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074",
        ),
        SourceClaim(
            claim="PR #9073 'Fix: respect backoff_factor in all provider retry paths' was merged on 2026-08-07 and references this issue with a closing keyword.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9073",
        ),
        SourceClaim(
            claim="PR #9448 was opened after the fix and closed as a duplicate of PR #9073.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9448",
        ),
        SourceClaim(
            claim="Last commit to dspy/predict/retry.py references the fix from PR #9073.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main/dspy/predict/retry.py",
        ),
    ],
    interpretation=[
        "The issue is closed and the fix has been in main since August 2026. There is no work remaining.",
        "PR #9448 shows another contributor attempted to re-fix this after the merge — it was closed as a duplicate, confirming the original fix was complete.",
        "The issue appearing in a search result is likely a stale cache or search-index lag.",
    ],
    prerequisites=[
        Prerequisite(text="Python 3.10+", status="have"),
        Prerequisite(text="Familiarity with DSPy's LM abstraction layer (dspy/predict/)", status="learn"),
        Prerequisite(text="Understanding of HTTP retry semantics (backoff, jitter)", status="learn"),
    ],
    blockers=[
        Blocker(
            text="Issue #9074 is closed. The fix merged in PR #9073 on 2026-08-07. No work remains.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9073",
        ),
        Blocker(
            text="A duplicate attempt (PR #9448) was already rejected by maintainers.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9448",
        ),
    ],
    activity_signals=[
        ActivitySignal(
            signal="DSPy had 12 commits to main in the last 7 days — project is actively maintained.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main",
        ),
        ActivitySignal(
            signal="Maintainer @okhat closed PR #9448 within 48 hours of it being opened.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9448",
        ),
        ActivitySignal(
            signal="4 PRs merged in the last week, including one touching the retry module.",
            source_url="https://github.com/stanfordnlp/dspy/pulls?q=is%3Apr+is%3Amerged",
        ),
    ],
    related_prs=[
        RelatedPR(
            url="https://github.com/stanfordnlp/dspy/pull/9073",
            state=PRState.merged,
        ),
        RelatedPR(
            url="https://github.com/stanfordnlp/dspy/pull/9448",
            state=PRState.closed,
        ),
    ],
    first_steps=[
        "Confirm the issue is closed before spending any time on it.",
        "Read PR #9073 to understand the fix that was applied.",
        "If you want to contribute to DSPy, look at the open issues list filtered by 'good first issue' for unclaimed items.",
        "The retry module is a good area to explore for related improvements not yet filed.",
    ],
    unknowns=[
        "Whether a second, related retry bug exists that was not covered by PR #9073.",
    ],
)

# ---------------------------------------------------------------------------
# Mock report index: keyed by issue_url.
# The mock investigate runner uses this dict; any URL not present fails with
# a clear error rather than returning a mismatched report.
# ---------------------------------------------------------------------------

MOCK_REPORTS: dict[str, InvestigationReport] = {
    MOCK_INVESTIGATION.issue_url: MOCK_INVESTIGATION,
}

# ---------------------------------------------------------------------------
# SSE progress sequences
# ---------------------------------------------------------------------------

DISCOVER_PROGRESS_STEPS = [
    "Building GitHub search queries from your profile...",
    "Opening GitHub issue search page...",
    "Extracting candidate issues from search results...",
    "Checking aggregator page for additional candidates...",
    "Validating issue URLs and removing duplicates...",
    "Ranking candidates against your profile...",
]

INVESTIGATE_PROGRESS_STEPS = [
    "Opening issue page...",
    "Reading issue description and discussion thread...",
    "Found linked PR — reading pull request details...",
    "Checking recent commits to affected files...",
    "Checking maintainer activity and response time...",
    "Writing personalised investigation report...",
]
