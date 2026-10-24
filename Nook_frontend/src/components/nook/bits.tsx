import { Check, Loader2, X, ExternalLink, WifiOff } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import type { JobEvent } from "@/lib/types";
import type { Phase } from "@/hooks/use-job";
import { cn } from "@/lib/utils";

export function shortLabel(url: string) {
  try {
    const u = new URL(url);
    if (u.hostname === "github.com") {
      const p = u.pathname.split("/").filter(Boolean);
      if (p.length >= 4 && p[2] === "issues") return `${p[0]}/${p[1]} #${p[3]}`;
      if (p.length >= 4 && p[2] === "pull") return `${p[0]}/${p[1]} PR #${p[3]}`;
      if (p.length >= 3) return `${p[0]}/${p[1]} · ${p[2]}`;
      return `github.com/${p.slice(0, 2).join("/")}`;
    }
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function SourceLink({ url, children }: { url: string; children?: ReactNode }) {
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 font-mono text-xs text-lime hover:underline"
    >
      {children ?? shortLabel(url)}
      <ExternalLink className="size-3" />
    </a>
  );
}

// Canonical 5-step stage labels — must match INVESTIGATE_PROGRESS_STEPS / DISCOVER_PROGRESS_STEPS
// order from the backend. We derive the current step index from how many events have arrived.
// Each step is shown as one line max.
const DISCOVER_STAGES = [
  "Building search queries",
  "Searching GitHub",
  "Extracting candidates",
  "Checking aggregators",
  "Ranking candidates",
];

const INVESTIGATE_STAGES = [
  "Opening issue",
  "Reading discussion",
  "Checking linked PRs",
  "Checking recent commits",
  "Writing report",
];

function useElapsed(running: boolean) {
  const [secs, setSecs] = useState(0);
  const ref = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!running) {
      if (ref.current) clearInterval(ref.current);
      return;
    }
    setSecs(0);
    ref.current = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => { if (ref.current) clearInterval(ref.current); };
  }, [running]);
  return secs;
}

export function ProgressSteps({
  events,
  phase,
  reconnecting,
  title,
  kind = "investigate",
}: {
  events: JobEvent[];
  phase: Phase;
  reconnecting: boolean;
  title: string;
  kind?: "discover" | "investigate";
}) {
  const stages = kind === "discover" ? DISCOVER_STAGES : INVESTIGATE_STAGES;
  // number of events received maps to steps completed
  const completedCount = Math.min(events.length, stages.length);
  const isRunning = phase === "streaming";
  const isFailed = phase === "failed";
  const isDone = phase === "done" || phase === "loading-result";
  // current active step index (0-based); -1 if not started
  const activeIdx = isDone ? stages.length - 1 : isRunning ? completedCount : -1;

  const elapsed = useElapsed(isRunning);
  const elapsedStr = elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`;

  return (
    <div className="panel bg-night">
      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="eyebrow">{title}</p>
        <span className="font-mono text-xs text-muted-foreground">
          {isRunning && elapsedStr}
          {isDone && "done"}
          {isFailed && "failed"}
        </span>
      </div>
      {reconnecting && isRunning && (
        <p className="mb-3 flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm text-pink">
          <WifiOff className="size-4" /> Reconnecting…
        </p>
      )}
      <ol className="space-y-2 font-mono text-sm">
        {stages.map((label, i) => {
          const done = isDone || i < completedCount;
          const active = !isDone && isRunning && i === completedCount;
          const failed = isFailed && i === completedCount;
          return (
            <li key={i} className="flex items-center gap-3 overflow-hidden">
              <span
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-full",
                  done && "bg-lime text-lime-foreground",
                  active && "bg-violet text-foreground",
                  failed && "bg-pink text-night",
                  !done && !active && !failed && "border border-border text-muted-foreground",
                )}
              >
                {done && <Check className="size-3" />}
                {active && <Loader2 className="size-3 animate-spin" />}
                {failed && <X className="size-3" />}
              </span>
              <span
                className={cn(
                  "truncate",
                  done && "text-muted-foreground",
                  active && "text-foreground font-semibold",
                  failed && "text-pink",
                  !done && !active && !failed && "text-muted-foreground/50",
                )}
              >
                {label}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function Tag({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "lime" | "pink" | "violet" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-0.5 text-xs font-medium",
        tone === "default" && "bg-night text-foreground",
        tone === "lime" && "bg-lime text-lime-foreground",
        tone === "pink" && "bg-pink text-night",
        tone === "violet" && "bg-violet-mid text-foreground",
      )}
    >
      {children}
    </span>
  );
}
