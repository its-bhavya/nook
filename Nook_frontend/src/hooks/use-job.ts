import { useCallback, useEffect, useState } from "react";
import { getJob, streamJob } from "@/lib/api";
import type { JobEvent } from "@/lib/types";

export type Phase = "streaming" | "loading-result" | "done" | "failed";

export function useJob<T>(jobId: string) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [phase, setPhase] = useState<Phase>("streaming");
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<T | null>(null);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setEvents([]);
    setPhase("streaming");
    setReconnecting(false);
    setError(null);
    setResult(null);
    const stop = streamJob(jobId, {
      onEvent: (e) => setEvents((prev) => [...prev, e]),
      onReconnecting: () => setReconnecting(true),
      onFailed: (m) => {
        setError(m);
        setPhase("failed");
      },
      onDone: async () => {
        setReconnecting(false);
        setPhase("loading-result");
        try {
          const job = await getJob<T>(jobId);
          if (job.status === "failed") throw new Error(job.error || "The job failed on the server.");
          setResult(job.result);
          setPhase("done");
        } catch (e) {
          setError(e instanceof Error ? e.message : "Couldn't load the final result.");
          setPhase("failed");
        }
      },
    });
    return stop;
  }, [jobId, attempt]);

  const retry = useCallback(() => setAttempt((a) => a + 1), []);
  return { events, phase, reconnecting, error, result, retry };
}
