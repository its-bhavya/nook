export type SkillLevel = "experienced" | "familiar" | "inferred";
export interface Skill {
  name: string;
  level: SkillLevel;
  evidence: string;
}
export interface Profile {
  skills: Skill[];
  interests: string[];
  goals: string;
  hours_per_week: number;
  spoken_languages: string[];
  environment: { has_llm_api_keys: string[]; os?: string | null };
}
export interface ProfileRequest {
  resume_text: string;
  github_url: string | null;
  interests: string[];
  goals: string;
  hours_per_week: number;
  spoken_languages: string[];
  environment: { has_llm_api_keys: string[]; os?: string | null };
}
export type ClaimantSignal = "none" | "claimed" | "linked_pr";
export interface Candidate {
  url: string;
  repo: string;
  title: string;
  labels: string[];
  opened: string;
  comments: number;
  claimant_signal: ClaimantSignal;
  source: "github_search" | "aggregator";
}
export type FitLevel = "strong" | "possible" | "weak";
export interface Report {
  issue_url: string;
  fit: { level: FitLevel; reasoning: string };
  sources_say: { claim: string; source_url: string }[];
  interpretation: string[];
  prerequisites: string[];
  blockers: { text: string; source_url: string | null }[];
  activity_signals: { signal: string; source_url: string }[];
  related_prs: { url: string; state: "open" | "merged" | "closed" }[];
  first_steps: string[];
  unknowns: string[];
}
export interface JobEvent {
  type?: string;
  message: string;
  timestamp: string;
  result?: unknown;
}
export type JobStatusValue = "queued" | "running" | "done" | "failed";
export interface JobStatus<T = unknown> {
  job_id: string;
  kind: "discover" | "investigate";
  status: JobStatusValue;
  events: JobEvent[];
  result: T | null;
  error?: string | null;
}
