import { createFileRoute, Link } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp, ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProgressSteps, SourceLink } from "@/components/nook/bits";
import { useJob } from "@/hooks/use-job";
import { useNook } from "@/lib/store";
import type { FitLevel, IssueState, PrerequisiteStatus, Report } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/investigate/$jobId")({
  head: () => ({
    meta: [
      { title: "Investigation report — Nook" },
      { name: "description", content: "What sources say, what Nook infers, and what's still unknown about one open-source issue." },
      { property: "og:title", content: "Investigation report — Nook" },
      { property: "og:description", content: "Is this open-source issue right for you? Nook's sourced investigation." },
    ],
  }),
  component: InvestigatePage,
});

const FIT: Record<FitLevel, { label: string; cls: string }> = {
  strong:   { label: "Strong fit",   cls: "bg-lime text-lime-foreground" },
  possible: { label: "Possible fit", cls: "bg-violet text-foreground" },
  weak:     { label: "Weak fit",     cls: "bg-pink text-night" },
};

const STATUS_STYLE: Record<IssueState, { label: string; cls: string }> = {
  open:        { label: "Open",           cls: "bg-lime/20 border border-lime text-lime" },
  claimed:     { label: "Claimed",        cls: "bg-amber-500/20 border border-amber-400 text-amber-300" },
  has_open_pr: { label: "Has open PR",    cls: "bg-amber-500/20 border border-amber-400 text-amber-300" },
  closed:      { label: "Closed",         cls: "bg-pink/20 border border-pink text-pink" },
  unknown:     { label: "Status unknown", cls: "bg-night border border-border text-muted-foreground" },
};

const PREREQ_LABEL: Record<PrerequisiteStatus, { label: string; cls: string }> = {
  have:    { label: "You have it",      cls: "bg-lime/20 border border-lime/40 text-lime" },
  learn:   { label: "You'd learn this", cls: "bg-violet/30 border border-violet/60 text-foreground" },
  unknown: { label: "Unknown",          cls: "bg-night border border-border text-muted-foreground" },
};

function repoFromUrl(url: string) {
  const m = url.match(/github\.com\/([^/]+\/[^/]+)/);
  return m?.[1] ?? url;
}

function Block({ title, sub, children, className }: {
  title: string; sub?: string; children: ReactNode; className?: string;
}) {
  return (
    <section className={cn("panel", className)}>
      <h2 className="text-xl font-semibold">{title}</h2>
      {sub && <p className="mt-1 text-xs text-muted-foreground">{sub}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>;
}

function SourcesSay({ items }: { items: Report["sources_say"] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, 3);
  return (
    <Block title="Sources say" sub="Facts taken directly from linked sources.">
      {items.length === 0 ? (
        <Empty>No sourced claims were found.</Empty>
      ) : (
        <>
          <ul className="space-y-4">
            {visible.map((s, i) => (
              <li key={i} className="text-sm">
                <p>{s.claim}</p>
                <div className="mt-1"><SourceLink url={s.source_url} /></div>
              </li>
            ))}
          </ul>
          {items.length > 3 && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="mt-4 flex items-center gap-1 text-xs text-lime hover:underline"
            >
              {expanded
                ? <><ChevronUp className="size-3" /> Show less</>
                : <><ChevronDown className="size-3" /> Show all {items.length} sources</>}
            </button>
          )}
        </>
      )}
    </Block>
  );
}

function InvestigatePage() {
  const { jobId } = Route.useParams();
  const { candidates, lastDiscoverJob } = useNook();
  const job = useJob<Report>(jobId);

  const url = job.result?.issue_url ?? null;
  const cand = url ? candidates[url] : undefined;
  const report: Report | null = job.result;
  const shortlistId = lastDiscoverJob;

  return (
    <div className="mx-auto max-w-5xl px-6 py-14">
      <p className="eyebrow mb-3">Investigation</p>
      <h1 className="display text-3xl md:text-5xl">{cand?.title ?? "Issue investigation"}</h1>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        {url && <span className="font-mono text-sm text-muted-foreground">{cand?.repo ?? repoFromUrl(url)}</span>}
        {url && <SourceLink url={url}>View issue</SourceLink>}
      </div>

      <div className="mt-8">
        <ProgressSteps
          title="Investigation progress"
          events={job.events}
          phase={job.phase}
          reconnecting={job.reconnecting}
          kind="investigate"
        />
      </div>

      {job.phase === "loading-result" && (
        <p className="mt-8 flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Putting the report together…
        </p>
      )}

      {job.phase === "failed" && (
        <div role="alert" className="panel mt-8">
          <h2 className="text-xl font-semibold text-pink">The investigation didn't finish</h2>
          <p className="mt-2 text-sm text-muted-foreground">{job.error}</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button onClick={job.retry}>Retry</Button>
          </div>
        </div>
      )}

      {job.phase === "done" && !report && (
        <div className="panel mt-8"><Empty>The investigation finished but returned no report.</Empty></div>
      )}

      {job.phase === "done" && report && (
        <div className="mt-10 space-y-6">

          {/* Verdict card */}
          <section className="panel bg-violet-deep">
            <span className={cn("rounded-md px-4 py-2 text-lg font-bold", FIT[report.fit.level].cls)}>
              {FIT[report.fit.level].label}
            </span>
            <p className="mt-4 text-sm font-medium">{report.summary}</p>
            <p className="mt-2 leading-7 text-muted-foreground">{report.fit.reasoning}</p>
          </section>

          {/* Status banner */}
          {report.issue_status && (() => {
            const key = report.issue_status.state as IssueState;
            const st = STATUS_STYLE[key] ?? STATUS_STYLE.unknown;
            return (
              <section className={cn("panel flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3", st.cls)}>
                <span className="font-semibold">{st.label}</span>
                {report.issue_status.note && (
                  <span className="text-sm">{report.issue_status.note}</span>
                )}
                {report.issue_status.source_url && (
                  <span className="ml-auto shrink-0">
                    <SourceLink url={report.issue_status.source_url} />
                  </span>
                )}
              </section>
            );
          })()}

          {/* Prerequisites */}
          <Block title="Prerequisites" sub="Status reflects your profile.">
            {report.prerequisites.length ? (
              <div className="flex flex-wrap gap-2">
                {report.prerequisites.map((p, i) => {
                  const st = PREREQ_LABEL[p.status as PrerequisiteStatus] ?? PREREQ_LABEL.unknown;
                  return (
                    <span key={i} className={cn("inline-flex flex-col rounded-md border px-3 py-1.5 text-xs", st.cls)}>
                      <span className="font-semibold">{st.label}</span>
                      <span className="mt-0.5 opacity-80">{p.text}</span>
                    </span>
                  );
                })}
              </div>
            ) : <Empty>No prerequisites listed.</Empty>}
          </Block>

          {/* Blockers */}
          <Block title="Blockers">
            {report.blockers.length ? (
              <ul className="space-y-3 text-sm">
                {report.blockers.map((b, i) => (
                  <li key={i}>
                    <p>{b.text}</p>
                    <div className="mt-1">
                      {b.source_url
                        ? <SourceLink url={b.source_url} />
                        : <span className="text-xs text-muted-foreground">No source found</span>}
                    </div>
                  </li>
                ))}
              </ul>
            ) : <Empty>No blockers found.</Empty>}
          </Block>

          {/* First steps */}
          <Block title="First steps">
            {report.first_steps.length ? (
              <ol className="list-decimal space-y-2 pl-5 text-sm">
                {report.first_steps.map((t, i) => <li key={i}>{t}</li>)}
              </ol>
            ) : <Empty>No suggested steps.</Empty>}
          </Block>

          {/* Sources say */}
          <SourcesSay items={report.sources_say} />

          {/* Unknowns */}
          {report.unknowns.length > 0 ? (
            <Block title="Unknowns" sub="Things Nook could not verify." className="border-pink/50">
              <ul className="list-disc space-y-3 pl-4 text-sm">
                {report.unknowns.map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </Block>
          ) : (
            <p className="text-xs text-muted-foreground">Nothing flagged.</p>
          )}

          <p className="text-sm text-muted-foreground">
            Nook can't guarantee an issue is easy, fits your schedule, or leads to an accepted contribution. The decision is yours.
          </p>

          <div className="flex flex-wrap gap-3">
            <Button asChild variant="lime">
              <a href={report.issue_url} target="_blank" rel="noopener noreferrer">
                Open issue on GitHub <ExternalLink />
              </a>
            </Button>
            {shortlistId && (
              <Button asChild variant="outline">
                <Link to="/results/$jobId" params={{ jobId: shortlistId }}>Back to shortlist</Link>
              </Button>
            )}
          </div>
        </div>
      )}

      {job.phase !== "done" && shortlistId && (
        <Button asChild variant="ghost" className="mt-8">
          <Link to="/results/$jobId" params={{ jobId: shortlistId }}>Back to shortlist</Link>
        </Button>
      )}
    </div>
  );
}
