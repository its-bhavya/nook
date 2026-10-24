// In-memory session state only (no saved history).
import { useSyncExternalStore } from "react";
import type { Candidate, Profile } from "./types";

export interface InvestigationEntry {
  jobId: string;
  url: string;
  title: string;
  repo: string;
}

interface State {
  demo: boolean;
  profile: Profile | null;
  candidates: Record<string, Candidate>;
  investigations: InvestigationEntry[];
  lastDiscoverJob: string | null;
}

let state: State = {
  demo: true,
  profile: null,
  candidates: {},
  investigations: [],
  lastDiscoverJob: null,
};
const listeners = new Set<() => void>();
const serverState = state;

export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  state = { ...state, ...(typeof patch === "function" ? patch(state) : patch) };
  listeners.forEach((l) => l());
}

export function getState() {
  return state;
}

export function useNook(): State {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
    () => serverState,
  );
}

export function addInvestigation(entry: InvestigationEntry) {
  setState((s) => ({
    investigations: [entry, ...s.investigations.filter((i) => i.jobId !== entry.jobId)],
  }));
}
