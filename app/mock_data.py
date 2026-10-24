"""
Realistic mock responses used when DEMO_MODE=true or when live services are not yet wired up.
All URLs reference real issues from CONTEXT.md section 14.
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
    PRState,
    Profile,
    RelatedPR,
    Skill,
    SkillLevel,
    SourceClaim,
    Environment,
)

# ---------------------------------------------------------------------------
# Mock profile (Persona A from CONTEXT.md section 14)
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
    ),
]

# ---------------------------------------------------------------------------
# Mock investigation: DSPy #9074 — the "already fixed" trap
# ---------------------------------------------------------------------------

MOCK_INVESTIGATION = InvestigationReport(
    issue_url="https://github.com/stanfordnlp/dspy/issues/9074",
    fit=FitAssessment(
        level=FitLevel.weak,
        reasoning=(
            "The underlying bug was resolved in PR #9041 which merged on 2026-09-30, "
            "roughly two weeks before this issue was filed as a duplicate. "
            "Taking this issue would mean re-implementing work that already exists. "
            "The DSPy codebase is a strong skill match (Python, agent frameworks), "
            "but there is nothing left to contribute here."
        ),
    ),
    sources_say=[
        SourceClaim(
            claim="PR #9041 'Fix: respect backoff_factor in all provider retry paths' was merged on 2026-09-30.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9041",
        ),
        SourceClaim(
            claim="Issue comment by maintainer @okhat on 2026-10-03: 'This looks like a duplicate of #9041 — closing once CI passes.'",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074#issuecomment-2391045",
        ),
        SourceClaim(
            claim="The issue is still marked 'Open' on GitHub as of the investigation date.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074",
        ),
        SourceClaim(
            claim="Last commit to dspy/predict/retry.py was 6 days ago, touching the exact lines in scope.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main/dspy/predict/retry.py",
        ),
    ],
    interpretation=[
        "The issue is open in state only — the fix has merged. GitHub does not auto-close issues when a PR merges unless the PR description contains a closing keyword.",
        "The maintainer comment strongly implies intent to close, but the issue was not closed before this investigation ran.",
        "Even if a second fix were possible, a duplicate contribution would be unlikely to be reviewed positively.",
    ],
    prerequisites=[
        "Python 3.10+",
        "Familiarity with DSPy's LM abstraction layer (dspy/predict/)",
        "Understanding of HTTP retry semantics (backoff, jitter)",
    ],
    blockers=[
        Blocker(
            text="The bug is already fixed in merged PR #9041. There is no remaining work for this issue.",
            source_url="https://github.com/stanfordnlp/dspy/pull/9041",
        ),
        Blocker(
            text="The issue may be officially closed by the time you read it.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074",
        ),
    ],
    activity_signals=[
        ActivitySignal(
            signal="DSPy had 12 commits to main in the last 7 days — project is actively maintained.",
            source_url="https://github.com/stanfordnlp/dspy/commits/main",
        ),
        ActivitySignal(
            signal="Maintainer @okhat replied to this issue within 48 hours of it being filed.",
            source_url="https://github.com/stanfordnlp/dspy/issues/9074#issuecomment-2391045",
        ),
        ActivitySignal(
            signal="4 PRs merged in the last week, including one touching the retry module.",
            source_url="https://github.com/stanfordnlp/dspy/pulls?q=is%3Apr+is%3Amerged",
        ),
    ],
    related_prs=[
        RelatedPR(
            url="https://github.com/stanfordnlp/dspy/pull/9041",
            state=PRState.merged,
        ),
    ],
    first_steps=[
        "Check the current state of the issue — it may already be closed.",
        "Read PR #9041 to understand the fix that was applied.",
        "If you want to contribute to DSPy, look at the open issues list filtered by 'good first issue' for un-claimed items.",
        "The retry module is a good area to explore for related improvements not yet filed.",
    ],
    unknowns=[
        "Whether the maintainer has already closed the issue after this investigation ran.",
        "Whether a second, related retry bug exists that was not covered by PR #9041.",
    ],
)

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
