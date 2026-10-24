"""
Pydantic models for Nook. Shapes are defined by CONTEXT.md section 11.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field, HttpUrl


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class SkillLevel(str, Enum):
    experienced = "experienced"
    familiar = "familiar"
    inferred = "inferred"


class ClaimantSignal(str, Enum):
    none = "none"
    claimed = "claimed"
    linked_pr = "linked_pr"


class IssueSource(str, Enum):
    github_search = "github_search"
    aggregator = "aggregator"


class FitLevel(str, Enum):
    strong = "strong"
    possible = "possible"
    weak = "weak"


class PRState(str, Enum):
    open = "open"
    merged = "merged"
    closed = "closed"


class JobKind(str, Enum):
    discover = "discover"
    investigate = "investigate"


class JobStatusEnum(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"


# ---------------------------------------------------------------------------
# Profile models
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    name: str = Field(..., description="Technology or skill name, e.g. 'FastAPI'")
    level: SkillLevel = Field(..., description="Confidence tier: experienced | familiar | inferred")
    evidence: str = Field(..., description="Short quote or project that supports this skill")


class Environment(BaseModel):
    has_llm_api_keys: list[str] = Field(
        default_factory=list,
        description="LLM providers the developer has API access to, e.g. ['Gemini', 'OpenAI']",
    )
    os: Optional[str] = Field(None, description="Operating system, if the developer chose to share it")


class Profile(BaseModel):
    skills: list[Skill] = Field(default_factory=list, description="Tiered skill list extracted from the resume")
    interests: list[str] = Field(default_factory=list, description="Topics the developer is curious about")
    goals: str = Field(..., description="Free-text description of what the developer wants from contributing")
    hours_per_week: int = Field(..., ge=1, description="Approximate hours available for open-source per week")
    spoken_languages: list[str] = Field(default_factory=list, description="Human languages the developer reads and writes")
    environment: Environment = Field(default_factory=Environment, description="Tools and API keys available to the developer")


# ---------------------------------------------------------------------------
# Request / response models for POST /profile
# ---------------------------------------------------------------------------

class ProfileRequest(BaseModel):
    resume_text: str = Field(..., description="Raw resume or bio pasted as plain text")
    github_url: Optional[str] = Field(None, description="Developer's GitHub profile URL (optional)")
    interests: list[str] = Field(default_factory=list, description="Topics the developer wants to work on")
    goals: str = Field("", description="What the developer hopes to get from contributing")
    hours_per_week: int = Field(5, ge=1, description="Approximate weekly hours available")
    spoken_languages: list[str] = Field(default_factory=list, description="Human languages the developer is comfortable with")


# ---------------------------------------------------------------------------
# Discovery models
# ---------------------------------------------------------------------------

class CandidateIssue(BaseModel):
    url: str = Field(..., description="Full GitHub issue URL, e.g. https://github.com/owner/repo/issues/123")
    repo: str = Field(..., description="Repository slug, e.g. owner/repo")
    title: str = Field(..., description="Issue title as shown on GitHub")
    labels: list[str] = Field(default_factory=list, description="Labels attached to the issue")
    opened: Optional[str] = Field(None, description="ISO 8601 date the issue was opened, or null if not visible")
    comments: int = Field(0, ge=0, description="Comment count visible on the search page")
    claimant_signal: ClaimantSignal = Field(ClaimantSignal.none, description="Visible claim or linked-PR indicator")
    source: IssueSource = Field(IssueSource.github_search, description="Where this issue was found")


class DiscoverRequest(BaseModel):
    profile: Profile = Field(..., description="Structured developer profile")
    query: Optional[str] = Field(None, description="Plain-English search string; omit to use profile-based discovery")


class DiscoverResponse(BaseModel):
    job_id: str = Field(..., description="Identifier for this discovery job; poll /jobs/{id} or stream /jobs/{id}/stream")


# ---------------------------------------------------------------------------
# Investigation models
# ---------------------------------------------------------------------------

class FitAssessment(BaseModel):
    level: FitLevel = Field(..., description="Overall fit verdict: strong | possible | weak")
    reasoning: str = Field(..., description="Plain-language explanation of the verdict")


class SourceClaim(BaseModel):
    claim: str = Field(..., description="What the source says, paraphrased or quoted briefly")
    source_url: str = Field(..., description="URL of the page that contains this claim")


class Blocker(BaseModel):
    text: str = Field(..., description="Description of the blocker")
    source_url: Optional[str] = Field(None, description="URL that evidences the blocker, or null if inferred")


class ActivitySignal(BaseModel):
    signal: str = Field(..., description="Observable repository activity observation")
    source_url: str = Field(..., description="URL of the page that shows this signal")


class RelatedPR(BaseModel):
    url: str = Field(..., description="Pull request URL")
    state: PRState = Field(..., description="PR state: open | merged | closed")


class InvestigationReport(BaseModel):
    issue_url: str = Field(..., description="The issue that was investigated")
    fit: FitAssessment = Field(..., description="Personalized fit verdict")
    sources_say: list[SourceClaim] = Field(default_factory=list, description="Sourced observations — what the pages actually say")
    interpretation: list[str] = Field(default_factory=list, description="Nook's inferences, clearly marked as interpretation")
    prerequisites: list[str] = Field(default_factory=list, description="Skills, tools or accounts needed before starting")
    blockers: list[Blocker] = Field(default_factory=list, description="Concrete obstacles, each linked to a source where possible")
    activity_signals: list[ActivitySignal] = Field(default_factory=list, description="Observable repository health signals with sources")
    related_prs: list[RelatedPR] = Field(default_factory=list, description="Pull requests linked to or relevant to this issue")
    first_steps: list[str] = Field(default_factory=list, description="Suggested concrete next actions for the developer")
    unknowns: list[str] = Field(default_factory=list, description="Things the agent could not verify")


class InvestigateRequest(BaseModel):
    issue_url: str = Field(..., description="Full GitHub issue URL to investigate")
    profile: Profile = Field(..., description="Developer profile used to personalise the report")


class InvestigateResponse(BaseModel):
    job_id: str = Field(..., description="Identifier for this investigation job")


# ---------------------------------------------------------------------------
# Job store models
# ---------------------------------------------------------------------------

class JobEvent(BaseModel):
    message: str = Field(..., description="Human-readable progress message")
    timestamp: str = Field(..., description="ISO 8601 timestamp of the event")


class JobStatus(BaseModel):
    job_id: str = Field(..., description="Unique job identifier")
    kind: JobKind = Field(..., description="Job type: discover | investigate")
    status: JobStatusEnum = Field(..., description="Current state: queued | running | done | failed")
    events: list[JobEvent] = Field(default_factory=list, description="Progress events emitted so far")
    result: Optional[Any] = Field(None, description="Final result payload when status is done; null otherwise")
