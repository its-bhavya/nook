// ============================================================
// MOCK DATA — demo mode only. Every URL in this file is mock data
// for demonstration and may not reflect the real GitHub state.
// ============================================================
import type { Candidate, JobEvent, JobStatus, Profile, Report } from "@/lib/types";
import personaA from "./profile.json";
import progress from "./progress_events.json";
import investigateJob from "./investigate_job.json";

export const DEMO_PREFIX = "demo-";

export const PERSONA_A = personaA as Profile;

export const PERSONA_B: Profile = {
  skills: [
    { name: "TypeScript", level: "experienced", evidence: "4 years building Node.js APIs" },
    { name: "Node.js", level: "experienced", evidence: "Maintains internal Express services" },
    { name: "React", level: "experienced", evidence: "Led dashboard rewrite" },
    { name: "PostgreSQL", level: "familiar", evidence: "Wrote migrations for billing service" },
    { name: "Docker", level: "familiar", evidence: "Local dev containers" },
    { name: "Python", level: "inferred", evidence: "Listed under 'other languages'" },
  ],
  interests: ["developer tooling", "SDKs", "testing"],
  goals: "Contribute regularly to one project and get to know its maintainers",
  hours_per_week: 15,
  spoken_languages: ["Spanish", "English"],
  environment: { has_llm_api_keys: [], os: "Linux" },
};

export type PersonaKey = "A" | "B";
export const PERSONAS: Record<PersonaKey, Profile> = { A: PERSONA_A, B: PERSONA_B };

// MOCK DATA: shortlist
export const DEMO_CANDIDATES: Candidate[] = [
  {
    url: "https://github.com/stanfordnlp/dspy/issues/9074",
    repo: "stanfordnlp/dspy",
    title: "Bug: LM retry logic ignores backoff parameter in certain providers",
    labels: ["bug", "good first issue"],
    opened: "2026-10-01",
    comments: 14,
    claimant_signal: "none",
    source: "github_search",
  },
  {
    url: "https://github.com/cubrid-lab/cubrid-cookbook-python/issues/447",
    repo: "cubrid-lab/cubrid-cookbook-python",
    title: "Add example: connection pooling with context managers",
    labels: ["beginner-friendly", "documentation"],
    opened: "2026-10-12",
    comments: 4,
    claimant_signal: "linked_pr",
    source: "aggregator",
  },
  {
    url: "https://github.com/supabase/supabase-py/issues/1806",
    repo: "supabase/supabase-py",
    title: "Implement missing `.count()` method on query builder",
    labels: ["good first issue"],
    opened: "2026-09-28",
    comments: 3,
    claimant_signal: "claimed",
    source: "github_search",
  },
  {
    url: "https://github.com/pydantic/pydantic-ai/issues/11061",
    repo: "pydantic/pydantic-ai",
    title: "LLM-API error handling: surface provider error codes to the caller",
    labels: ["good first issue", "enhancement"],
    opened: "2026-10-15",
    comments: 5,
    claimant_signal: "none",
    source: "github_search",
  },
  {
    url: "https://github.com/NVIDIA/garak/issues/2336",
    repo: "NVIDIA/garak",
    title: "Add missing type annotations to probe base class",
    labels: ["good first issue", "typing"],
    opened: "2026-10-10",
    comments: 2,
    claimant_signal: "none",
    source: "github_search",
  },
];

const dspyA = investigateJob.result as Report;

// MOCK DATA: reports per issue, per persona
export const DEMO_REPORTS: Record<string, Record<PersonaKey, Report>> = {
  [dspyA.issue_url]: {
    A: dspyA,
    B: {
      ...dspyA,
      fit: {
        level: "weak",
        reasoning:
          "The fix already merged in PR #9041, so there is nothing left to do here. On top of that, DSPy is a Python codebase and Python is only mentioned on your resume, and reproducing the bug needs an LLM API key you don't have.",
      },
      prerequisites: [
        "Working Python environment (3.10+)",
        "An LLM provider API key to reproduce retry behaviour",
      ],
      blockers: [
        {
          text: "Fix already merged in PR #9041.",
          source_url: "https://github.com/stanfordnlp/dspy/pull/9041",
        },
        { text: "No LLM API key available to reproduce the behaviour.", source_url: null },
      ],
      unknowns: [
        "Whether the maintainer has already closed the issue after this investigation ran.",
        "Whether DSPy has a mock provider that avoids needing a real API key.",
      ],
    },
  },
  "https://github.com/NVIDIA/garak/issues/2336": {
    A: {
      issue_url: "https://github.com/NVIDIA/garak/issues/2336",
      fit: {
        level: "strong",
        reasoning:
          "The change is limited to one file, needs only Python typing knowledge you've used for years, and has no linked PR or claim. It fits comfortably in a 6-hour week, and the maintainer asked for exactly this kind of help.",
      },
      sources_say: [
        {
          claim: "The issue asks for type hints on public methods of garak/probes/base.py only.",
          source_url: "https://github.com/NVIDIA/garak/issues/2336",
        },
        {
          claim: "Maintainer @leondz commented: 'Happy to review a PR for this, keep it to base.py.'",
          source_url: "https://github.com/NVIDIA/garak/issues/2336#issuecomment-2400111",
        },
        {
          claim: "CONTRIBUTING.md says type-only changes don't need new tests.",
          source_url: "https://github.com/NVIDIA/garak/blob/main/CONTRIBUTING.md",
        },
      ],
      interpretation: [
        "The scope is small and has been confirmed by a maintainer, so the risk of the request changing is low.",
        "Nobody seems to be working on it: no linked PR and no 'I'll take this' comments.",
        "Running the probes is not needed to add annotations, so no API key is required.",
      ],
      prerequisites: ["Python 3.10+", "Familiarity with typing generics", "mypy installed locally"],
      blockers: [],
      activity_signals: [
        {
          signal: "Maintainers replied to new issues within 2 days on average this month.",
          source_url: "https://github.com/NVIDIA/garak/issues?q=is%3Aissue+sort%3Aupdated-desc",
        },
        {
          signal: "11 PRs merged in the last two weeks.",
          source_url: "https://github.com/NVIDIA/garak/pulls?q=is%3Apr+is%3Amerged",
        },
      ],
      related_prs: [],
      first_steps: [
        "Comment on the issue saying you'd like to take it.",
        "Fork the repo and run mypy on garak/probes/base.py to see current gaps.",
        "Add annotations to the public methods only.",
        "Open a draft PR that references #2336.",
      ],
      unknowns: ["Whether CI runs mypy in strict mode, which could surface unrelated errors."],
    },
    B: {
      issue_url: "https://github.com/NVIDIA/garak/issues/2336",
      fit: {
        level: "possible",
        reasoning:
          "The task is small and clearly scoped, and you have plenty of hours. But it's Python typing work, and Python only appears on your resume without examples. Your TypeScript typing experience will help, so expect a learning curve rather than a blocker.",
      },
      sources_say: [
        {
          claim: "The issue asks for type hints on public methods of garak/probes/base.py only.",
          source_url: "https://github.com/NVIDIA/garak/issues/2336",
        },
        {
          claim: "CONTRIBUTING.md says type-only changes don't need new tests.",
          source_url: "https://github.com/NVIDIA/garak/blob/main/CONTRIBUTING.md",
        },
      ],
      interpretation: [
        "Python's type hints work a lot like TypeScript types, so the ideas will carry over.",
        "You won't need an LLM API key, because nothing has to be run against a model.",
      ],
      prerequisites: [
        "Python 3.10+ installed",
        "Basic Python typing syntax (typing module, Optional, generics)",
        "mypy installed locally",
      ],
      blockers: [
        { text: "Python is listed on your resume but not shown in any project.", source_url: null },
      ],
      activity_signals: [
        {
          signal: "Maintainers replied to new issues within 2 days on average this month.",
          source_url: "https://github.com/NVIDIA/garak/issues?q=is%3Aissue+sort%3Aupdated-desc",
        },
      ],
      related_prs: [],
      first_steps: [
        "Skim the Python typing docs for 30 minutes.",
        "Comment on the issue to say you'd like to take it.",
        "Run mypy on garak/probes/base.py.",
        "Open a draft PR early and ask for feedback.",
      ],
      unknowns: [
        "Whether CI runs mypy in strict mode.",
        "How long review takes for first-time contributors.",
      ],
    },
  },
};

const discoverEvents = (progress.discover as JobEvent[]).filter((e) => e.type !== "done");
const investigateEvents = (progress.investigate as JobEvent[]).filter((e) => e.type !== "done");

export const demoDiscoverJobId = () => `${DEMO_PREFIX}discover`;
export const demoInvestigateJobId = (url: string) =>
  `${DEMO_PREFIX}investigate-${btoa(url).replace(/=+$/, "").replace(/\//g, "_").replace(/\+/g, "-")}`;

export function demoIssueUrl(jobId: string): string | null {
  const enc = jobId.slice(`${DEMO_PREFIX}investigate-`.length).replace(/_/g, "/").replace(/-/g, "+");
  try {
    return atob(enc + "===".slice((enc.length + 3) % 4));
  } catch {
    return null;
  }
}

export function demoJob(id: string): JobStatus {
  if (id === demoDiscoverJobId()) {
    return { job_id: id, kind: "discover", status: "done", events: discoverEvents, result: DEMO_CANDIDATES };
  }
  const url = demoIssueUrl(id);
  const reports = url ? DEMO_REPORTS[url] : undefined;
  if (!reports) {
    return {
      job_id: id,
      kind: "investigate",
      status: "failed",
      events: investigateEvents.slice(0, 1),
      result: null,
      error: "There's no saved demo report for this issue. Demo reports exist for stanfordnlp/dspy #9074 and NVIDIA/garak #2336.",
    };
  }
  return { job_id: id, kind: "investigate", status: "done", events: investigateEvents, result: reports.A };
}
