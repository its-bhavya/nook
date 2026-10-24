// All network access lives here. Point the app at the real backend via VITE_API_URL.
import type { Candidate, JobEvent, JobStatus, Profile, ProfileRequest, Report } from "./types";

export const API_BASE_URL =
  (typeof import.meta !== "undefined" && (import.meta as Record<string, unknown>).env
    ? ((import.meta as unknown as { env: Record<string, string> }).env.VITE_API_URL)
    : undefined) ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      mode: "cors",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
    });
  } catch {
    throw new Error(`Couldn't reach the Nook server at ${API_BASE_URL}.`);
  }
  if (!res.ok) {
    let detail = "";
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : (body.detail?.[0]?.msg ?? "");
    } catch {
      /* ignore */
    }
    throw new Error(`The server answered ${res.status}${detail ? `: ${detail}` : ""}.`);
  }
  return res.json() as Promise<T>;
}

export async function buildProfile(body: ProfileRequest): Promise<Profile> {
  return request<Profile>("/profile", { method: "POST", body: JSON.stringify(body) });
}

export async function startDiscover(profile: Profile, query: string | null) {
  return request<{ job_id: string }>("/discover", {
    method: "POST",
    body: JSON.stringify(query ? { profile, query } : { profile }),
  });
}

export async function startInvestigate(issue_url: string, profile: Profile) {
  return request<{ job_id: string }>("/investigate", {
    method: "POST",
    body: JSON.stringify({ issue_url, profile }),
  });
}

export async function getJob<T = Candidate[] | Report>(id: string): Promise<JobStatus<T>> {
  return request<JobStatus<T>>(`/jobs/${encodeURIComponent(id)}`);
}

export interface StreamHandlers {
  onEvent: (e: JobEvent) => void;
  onDone: () => void;
  onFailed: (message: string) => void;
  onReconnecting: () => void;
}

/** Streams job progress via SSE. On drop, reports "reconnecting" and falls back to polling. */
export function streamJob(id: string, h: StreamHandlers): () => void {
  let closed = false;
  const timers: ReturnType<typeof setTimeout>[] = [];
  let seen = 0;

  const poll = async () => {
    if (closed) return;
    try {
      const job = await getJob(id);
      job.events.slice(seen).forEach(h.onEvent);
      seen = job.events.length;
      if (job.status === "done") return h.onDone();
      if (job.status === "failed") return h.onFailed(job.error || "The job failed on the server.");
    } catch {
      /* keep polling */
    }
    timers.push(setTimeout(poll, 2000));
  };

  interface SseMsg { type?: string; message: string; timestamp: string; }
  const es = new EventSource(`${API_BASE_URL}/jobs/${encodeURIComponent(id)}/stream`);
  es.onmessage = (msg) => {
    try {
      const e = JSON.parse(msg.data) as SseMsg;
      if (e.type === "done") {
        es.close();
        h.onDone();
      } else if (e.type === "error" || e.type === "failed") {
        es.close();
        h.onFailed(e.message);
      } else {
        seen += 1;
        h.onEvent({ message: e.message, timestamp: e.timestamp });
      }
    } catch {
      /* ignore malformed */
    }
  };
  es.onerror = () => {
    if (closed) return;
    es.close();
    h.onReconnecting();
    poll();
  };

  return () => {
    closed = true;
    es.close();
    timers.forEach(clearTimeout);
  };
}
