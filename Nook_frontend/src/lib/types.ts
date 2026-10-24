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
  opened: string | null;
  comments: number;
  claimant_signal: ClaimantSignal;
  source: "github_search" | "aggregator";
  theme: string | null;
  matched_skills: string[];
  reason: string | null;
}
export type FitLevel = "strong" | "possible" | "weak";
export type IssueState = "open" | "closed" | "claimed" | "has_open_pr" | "unknown";
export type PrerequisiteStatus = "have" | "learn" | "unknown";
export interface IssueStatus {
  state: IssueState;
  note: string;
  source_url: string | null;
}
export interface FitAssessment {
  level: FitLevel;
  reasoning: string;
}
export interface SourceClaim {
  claim: string;
  source_url: string;
}
export interface Prerequisite {
  text: string;
  status: PrerequisiteStatus;
}
export interface Blocker {
  text: string;
  source_url: string | null;
}
export interface ActivitySignal {
  signal: string;
  source_url: string;
}
export interface RelatedPR {
  url: string;
  state: "open" | "merged" | "closed";
}
export interface Report {
  issue_url: string;
  summary: string;
  issue_status: IssueStatus;
  fit: FitAssessment;
  sources_say: SourceClaim[];
  interpretation: string[];
  prerequisites: Prerequisite[];
  blockers: Blocker[];
  activity_signals: ActivitySignal[];
  related_prs: RelatedPR[];
  first_steps: string[];
  unknowns: string[];
}
export interface JobEvent {
  message: string;
  timestamp: string;
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
