import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { ExternalLink, Loader2, MessageSquare, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ProgressSteps, Tag } from "@/components/nook/bits";
import { useJob } from "@/hooks/use-job";
import { isDemoJob, startInvestigate } from "@/lib/api";
import { addInvestigation, setState, useNook } from "@/lib/store";
import { PERSONA_A } from "@/mock/demo";
import type { Candidate } from "@/lib/types";

export const Route = createFileRoute("/results/$jobId")({
  head: () => ({
    meta: [
      { title: "Your shortlist — Nook" },
      { name: "description", content: "Open-source issues Nook shortlisted for your profile, with live search progress." },
      { property: "og:title", content: "Your shortlist — Nook" },
      { property: "og:description", content: "Open-source issues Nook shortlisted for your profile." },
    ],
  }),
  component: ResultsPage,
});

const CLAIM_TEXT: Record<Candidate["claimant_signal"], string> = {
  none: "No one has claimed it",
  claimed: "Someone has said they're working on it",
  linked_pr: "A pull request is already linked",
};

function reasonFor(c: Candidate) {
  const parts = [c.source === "aggregator" ? "Found on an issue aggregator" : "Matched your GitHub search"];
  if (c.labels.length) parts.push(`labeled “${c.labels[0]}”`);
  parts.push(c.comments <= 5 ? "with a short discussion" : "with an active discussion");
  return parts.join(", ") + ".";
}

function ResultsPage() {
  const { jobId } = Route.useParams();
  const navigate = useNavigate();
  const { profile, investigations } = useNook();
  const job = useJob<Candidate[]>(jobId);
  const [pending, setPending] = useState<string | null>(null);
  const [invError, setInvError] = useState<string | null>(null);

  useEffect(() => {
    if (job.result) {
      setState((s) => ({
        candidates: { ...s.candidates, ...Object.fromEntries(job.result!.map((c) => [c.url, c])) },
      }));
    }
  }, [job.result]);

  const demo = isDemoJob(jobId);
  const investigate = async (c: Candidate) => {
    const p = profile ?? (demo ? PERSONA_A : null);
    if (!p) {
      setInvError("Your profile isn't loaded anymore. Go back and build it again to investigate.");
      return;
    }
    setInvError(null);
    setPending(c.url);
    try {
      const { job_id } = await startInvestigate(c.url, p, demo);
      addInvestigation({ jobId: job_id, url: c.url, title: c.title, repo: c.repo });
      navigate({ to: "/investigate/$jobId", params: { jobId: job_id } });
    } catch (e) {
      setInvError(`Couldn't start the investigation. ${e instanceof Error ? e.message : ""}`);
      setPending(null);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-14">
      <p className="eyebrow mb-3">Shortlist</p>
      <h1 className="display mb-8 text-4xl md:text-6xl">
        Issues worth a <span className="keyword">closer look</span>
      </h1>

      <ProgressSteps title="Discovery progress" events={job.events} phase={job.phase} reconnecting={job.reconnecting} />

      {investigations.length > 0 && (
        <div className="mt-6 rounded-xl bg-night p-4">
          <p className="eyebrow mb-2">Your investigations</p>
          <ul className="space-y-1 text-sm">
            {investigations.map((i) => (
              <li key={i.jobId}>
                <Link to="/investigate/$jobId" params={{ jobId: i.jobId }} className="text-lime hover:underline">
                  {i.repo} — {i.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {job.phase === "loading-result" && (
        <p className="mt-8 flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading your shortlist…
        </p>
      )}

      {job.phase === "failed" && (
        <div role="alert" className="panel mt-8 border-pink">
          <h2 className="text-xl font-semibold text-pink">Discovery didn't finish</h2>
          <p className="mt-2 text-sm text-muted-foreground">{job.error}</p>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button onClick={job.retry}>Retry</Button>
            <Button
              variant="outline"
              onClick={() => {
                setState({ demo: true });
                navigate({ to: "/results/$jobId", params: { jobId: "demo-discover" } });
              }}
            >
              Switch to demo mode
            </Button>
          </div>
        </div>
      )}

      {invError && <p role="alert" className="mt-6 text-sm text-pink">{invError}</p>}

      {job.phase === "done" && (
        <>
          {(!job.result || job.result.length === 0) ? (
            <div className="panel mt-8 text-center">
              <Search className="mx-auto mb-3 size-8 text-muted-foreground" />
              <p className="text-lg">No open issues matched. Try broadening your search.</p>
              <Button asChild variant="outline" className="mt-4">
                <Link to="/">Edit search</Link>
              </Button>
            </div>
          ) : (
            <ul className="mt-8 grid gap-5">
              {job.result.map((c) => (
                <li key={c.url} className="panel">
                  <div className="flex flex-wrap items-start justify-between gap-4">
                    <div className="min-w-0">
                      <p className="font-mono text-sm text-muted-foreground">{c.repo}</p>
                      <h2 className="mt-1 text-xl font-medium">{c.title}</h2>
                    </div>
                    <a
                      href={c.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-xs text-lime hover:underline"
                    >
                      github.com <ExternalLink className="size-3" />
                    </a>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {c.labels.map((l) => <Tag key={l} tone="violet">{l}</Tag>)}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
                    <span>Opened {c.opened}</span>
                    <span className="inline-flex items-center gap-1"><MessageSquare className="size-3" /> {c.comments} comments</span>
                    <span className={c.claimant_signal === "none" ? "text-lime" : "text-pink"}>{CLAIM_TEXT[c.claimant_signal]}</span>
                  </div>
                  <p className="mt-3 text-sm">{reasonFor(c)}</p>
                  <Button className="mt-5" onClick={() => investigate(c)} disabled={pending === c.url}>
                    {pending === c.url && <Loader2 className="animate-spin" />}
                    Investigate this issue
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      <p className="mt-10 text-sm text-muted-foreground">
        Labels like 'good first issue' don't tell you if an issue is right for you. Investigate before you commit.
      </p>
    </div>
  );
}
