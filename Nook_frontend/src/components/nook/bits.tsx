import { Check, Loader2, X, ExternalLink, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
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

export function ProgressSteps({
  events,
  phase,
  reconnecting,
  title,
}: {
  events: JobEvent[];
  phase: Phase;
  reconnecting: boolean;
  title: string;
}) {
  const finished = phase === "done" || phase === "loading-result";
  return (
    <div className="panel bg-night">
      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="eyebrow">{title}</p>
        <span className="font-mono text-xs text-muted-foreground">
          {phase === "streaming" && "running"}
          {phase === "loading-result" && "loading results"}
          {phase === "done" && "done"}
          {phase === "failed" && "failed"}
        </span>
      </div>
      {reconnecting && phase === "streaming" && (
        <p className="mb-3 flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm text-pink">
          <WifiOff className="size-4" /> Live connection dropped. Reconnecting and checking for updates…
        </p>
      )}
      {events.length === 0 && phase === "streaming" && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Starting the agent…
        </p>
      )}
      <ol className="space-y-2 font-mono text-sm">
        {events.map((e, i) => {
          const last = i === events.length - 1;
          const state = finished || !last ? "done" : phase === "failed" ? "failed" : "running";
          return (
            <li key={i} className="flex items-start gap-3">
              <span
                className={cn(
                  "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
                  state === "done" && "bg-lime text-lime-foreground",
                  state === "running" && "bg-violet text-foreground",
                  state === "failed" && "bg-pink text-night",
                )}
              >
                {state === "done" && <Check className="size-3" />}
                {state === "running" && <Loader2 className="size-3 animate-spin" />}
                {state === "failed" && <X className="size-3" />}
              </span>
              <span className={cn(state === "done" ? "text-muted-foreground" : "text-foreground")}>
                {e.message.replace(/\.\.\.$/, "")}
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
