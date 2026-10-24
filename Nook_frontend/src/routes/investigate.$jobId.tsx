import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState, type ReactNode } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProgressSteps, SourceLink, Tag } from "@/components/nook/bits";
import { useJob } from "@/hooks/use-job";
import { isDemoJob } from "@/lib/api";
import { setState, useNook } from "@/lib/store";
import { DEMO_REPORTS, demoIssueUrl, type PersonaKey } from "@/mock/demo";
import type { FitLevel, Report } from "@/lib/types";
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
  strong: { label: "Strong fit", cls: "bg-lime text-lime-foreground" },
  possible: { label: "Possible fit", cls: "bg-violet text-foreground" },
  weak: { label: "Weak fit", cls: "bg-pink text-night" },
};

function repoFromUrl(url: string) {
  const m = url.match(/github\.com\/([^/]+\/[^/]+)/);
  return m?.[1] ?? url;
}

function Block({ title, sub, children, className }: { title: string; sub?: string; children: ReactNode; className?: string }) {
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

function InvestigatePage() {
  const { jobId } = Route.useParams();
  const navigate = useNavigate();
  const { candidates, lastDiscoverJob } = useNook();
  const job = useJob<Report>(jobId);
  const demo = isDemoJob(jobId);
  const [persona, setPersona] = useState<PersonaKey>("A");

  const url = job.result?.issue_url ?? (demo ? demoIssueUrl(jobId) : null);
  const cand = url ? candidates[url] : undefined;
  const report: Report | null =
    demo && url && DEMO_REPORTS[url] ? DEMO_REPORTS[url][persona] : job.result;
  const shortlistId = lastDiscoverJob ?? (demo ? "demo-discover" : null);

  return (
    <div className="mx-auto max-w-5xl px-6 py-14">
      <p className="eyebrow mb-3">Investigation</p>
      <h1 className="display text-3xl md:text-5xl">{cand?.title ?? "Issue investigation"}</h1>
      <div className="mt-4 flex flex-wrap items-center gap-4">
        {url && <span className="font-mono text-sm text-muted-foreground">{cand?.repo ?? repoFromUrl(url)}</span>}
        {url && <SourceLink url={url}>View issue</SourceLink>}
        <div className="ml-auto flex items-center gap-1 rounded-lg bg-night p-1" role="group" aria-label="Profile">
          {demo ? (
            (["A", "B"] as const).map((p) => (
              <button
                key={p}
                onClick={() => setPersona(p)}
                className={cn(
                  "rounded-md px-3 py-1.5 text-xs font-semibold",
                  persona === p ? "bg-primary text-primary-foreground" : "text-muted-foreground",
                )}
              >
                Persona {p}
              </button>
            ))
          ) : (
            <span className="px-3 py-1.5 text-xs text-muted-foreground">Your profile</span>
          )}
        </div>
      </div>

      <div className="mt-8">
        <ProgressSteps title="Investigation progress" events={job.events} phase={job.phase} reconnecting={job.reconnecting} />
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
            {!demo && (
              <Button
                variant="outline"
                onClick={() => {
                  setState({ demo: true });
                  navigate({ to: "/results/$jobId", params: { jobId: "demo-discover" } });
                }}
              >
                Switch to demo mode
              </Button>
            )}
          </div>
        </div>
      )}

      {job.phase === "done" && !report && (
        <div className="panel mt-8"><Empty>The investigation finished but returned no report.</Empty></div>
      )}

      {job.phase === "done" && report && (
        <div className="mt-10 space-y-6">
          <section className="panel flex flex-col gap-4 bg-violet-deep md:flex-row md:items-start">
            <span className={cn("shrink-0 rounded-md px-4 py-2 text-lg font-bold", FIT[report.fit.level].cls)}>
              {FIT[report.fit.level].label}
            </span>
            <p className="leading-7">{report.fit.reasoning}</p>
          </section>

          <div className="grid gap-6 md:grid-cols-3">
            <Block title="Sources say" sub="Facts taken directly from linked sources.">
              {report.sources_say.length ? (
                <ul className="space-y-4">
                  {report.sources_say.map((s, i) => (
                    <li key={i} className="text-sm">
                      <p>{s.claim}</p>
                      <div className="mt-1"><SourceLink url={s.source_url} /></div>
                    </li>
                  ))}
                </ul>
              ) : <Empty>No sourced claims were found.</Empty>}
            </Block>
            <Block title="Nook's interpretation" sub="These are Nook's inferences, not verified facts." className="border-dashed">
              {report.interpretation.length ? (
                <ul className="list-disc space-y-3 pl-4 text-sm italic">
                  {report.interpretation.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              ) : <Empty>Nook made no inferences for this issue.</Empty>}
            </Block>
            <Block title="Unknowns" sub="Things Nook could not verify." className="border-pink">
              {report.unknowns.length ? (
                <ul className="list-disc space-y-3 pl-4 text-sm">
                  {report.unknowns.map((t, i) => <li key={i}>{t}</li>)}
                </ul>
              ) : <Empty>Nothing flagged as unverified</Empty>}
            </Block>
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <Block title="Prerequisites">
              {report.prerequisites.length ? (
                <ul className="list-disc space-y-2 pl-4 text-sm">{report.prerequisites.map((t, i) => <li key={i}>{t}</li>)}</ul>
              ) : <Empty>No prerequisites listed.</Empty>}
            </Block>
            <Block title="Blockers">
              {report.blockers.length ? (
                <ul className="space-y-3 text-sm">
                  {report.blockers.map((b, i) => (
                    <li key={i}>
                      <p>{b.text}</p>
                      <div className="mt-1">
                        {b.source_url ? <SourceLink url={b.source_url} /> : <span className="text-xs text-muted-foreground">No source found</span>}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : <Empty>No blockers found.</Empty>}
            </Block>
            <Block title="Repository activity signals" sub="These are context, not a verdict on project health.">
              {report.activity_signals.length ? (
                <ul className="space-y-3 text-sm">
                  {report.activity_signals.map((s, i) => (
                    <li key={i}>
                      <p>{s.signal}</p>
                      <div className="mt-1"><SourceLink url={s.source_url} /></div>
                    </li>
                  ))}
                </ul>
              ) : <Empty>No activity signals collected.</Empty>}
            </Block>
            <Block title="Related pull requests">
              {report.related_prs.length ? (
                <ul className="space-y-2">
                  {report.related_prs.map((p) => (
                    <li key={p.url} className="flex items-center gap-3">
                      <Tag tone={p.state === "merged" ? "lime" : p.state === "open" ? "violet" : "pink"}>{p.state}</Tag>
                      <SourceLink url={p.url} />
                    </li>
                  ))}
                </ul>
              ) : <Empty>No related pull requests found.</Empty>}
            </Block>
          </div>

          <Block title="Suggested first steps">
            {report.first_steps.length ? (
              <ol className="list-decimal space-y-2 pl-5 text-sm">{report.first_steps.map((t, i) => <li key={i}>{t}</li>)}</ol>
            ) : <Empty>No suggested steps.</Empty>}
          </Block>

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
