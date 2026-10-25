import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Plus, Trash2, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TagInput } from "@/components/nook/tag-input";
import { buildProfile, parseResume, startDiscover } from "@/lib/api";
import { setState, useNook } from "@/lib/store";
import type { Profile, SkillLevel } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Nook — Build your profile and find issues" },
      { name: "description", content: "Paste your resume, set your preferences and let Nook find open-source issues that fit you." },
      { property: "og:title", content: "Nook — Find your place in open source" },
      { property: "og:description", content: "Paste your resume, set your preferences and let Nook find open-source issues that fit you." },
    ],
  }),
  component: ProfilePage,
});

const LEARN = ["Stick to what I know", "Happy to learn a little", "Keen to learn something new"];
const GROUPS: { level: SkillLevel; label: string }[] = [
  { level: "experienced", label: "Experienced" },
  { level: "familiar", label: "Familiar" },
  { level: "inferred", label: "Inferred" },
];

function Label({ htmlFor, children, hint }: { htmlFor?: string; children: React.ReactNode; hint?: string }) {
  return (
    <label htmlFor={htmlFor} className="mb-2 block">
      <span className="block text-sm font-semibold">{children}</span>
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
    </label>
  );
}

function ProfilePage() {
  const navigate = useNavigate();
  const { profile, prefetchDiscoverJob } = useNook();
  const [resume, setResume] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [parsing, setParsing] = useState(false);
  const [github, setGithub] = useState("");
  const [hours, setHours] = useState(6);
  const [languages, setLanguages] = useState<string[]>([]);
  const [keys, setKeys] = useState<string[]>([]);
  const [os, setOs] = useState("");
  const [learn, setLearn] = useState(LEARN[1]);
  const [mode, setMode] = useState<"search" | "profile">("search");
  const [query, setQuery] = useState("");
  const [building, setBuilding] = useState(false);
  const [finding, setFinding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const busy = parsing || building;

  const setProfile = (p: Profile) => setState({ profile: p });

  const onBuild = async () => {
    setError(null);
    let resumeText = resume;
    if (resumeFile) {
      setParsing(true);
      try {
        resumeText = await parseResume(resumeFile);
        setResume(resumeText);
      } catch (e) {
        setError(`Couldn't parse your PDF. ${e instanceof Error ? e.message : ""}`);
        setParsing(false);
        return;
      } finally {
        setParsing(false);
      }
    }
    if (!resumeText.trim()) {
      setError("Upload your resume PDF first, so Nook has something to read.");
      return;
    }
    setBuilding(true);
    try {
      const p = await buildProfile({
        resume_text: resumeText,
        github_url: github.trim() || null,
        interests: [],
        goals: `Willingness to learn: ${learn}`,
        hours_per_week: Math.max(1, hours),
        spoken_languages: languages,
        environment: { has_llm_api_keys: keys, os: os.trim() || null },
      });
      setProfile(p);
      // Fire a profile-based discover job immediately in the background
      try {
        const { job_id } = await startDiscover(p, null);
        setState({ prefetchDiscoverJob: job_id });
      } catch {
        // Non-fatal — user can still manually trigger discover
      }
    } catch (e) {
      setError(`Couldn't build your profile. ${e instanceof Error ? e.message : ""}`);
    } finally {
      setBuilding(false);
    }
  };

  const onFind = async () => {
    if (!profile) return;
    setError(null);
    if (mode === "search" && !query.trim()) {
      setError("Describe what you're looking for, or switch to profile-based discovery.");
      return;
    }
    setFinding(true);
    try {
      let job_id: string;
      if (mode === "profile" && prefetchDiscoverJob) {
        // Prefetch job already running — go straight to results
        job_id = prefetchDiscoverJob;
      } else {
        const res = await startDiscover(profile, mode === "search" ? query.trim() : null);
        job_id = res.job_id;
      }
      setState({ lastDiscoverJob: job_id, prefetchDiscoverJob: null });
      navigate({ to: "/results/$jobId", params: { jobId: job_id } });
    } catch (e) {
      setError(`Couldn't start discovery. ${e instanceof Error ? e.message : ""}`);
      setFinding(false);
    }
  };

  return (
    <div className="mx-auto max-w-5xl px-6 py-14">
      <section className="mb-12">
        <p className="eyebrow mb-4">Step 1 · Your profile</p>
        <h1 className="display text-5xl md:text-7xl">
          Find your place in <span className="keyword">open source</span>.
        </h1>
        <p className="mt-6 max-w-2xl text-lg leading-8 text-muted-foreground">
          Tell Nook who you are. It finds issues, then investigates whether one is actually right for you.
        </p>
      </section>

      <div className="panel grid gap-6 md:grid-cols-2">
        <div className="md:col-span-2">
          <Label htmlFor="resume" hint="PDF only. Remove personal contact details before uploading.">
            Resume
          </Label>
          <label
            htmlFor="resume"
            className={cn(
              "flex min-h-24 cursor-pointer flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed border-border bg-night px-4 py-6 text-sm text-muted-foreground transition-colors hover:border-foreground/40",
              resumeFile && "border-solid border-lime/60 text-foreground",
            )}
          >
            {parsing ? (
              <Loader2 className="size-6 shrink-0 animate-spin text-lime" />
            ) : (
              <UploadCloud className="size-6 shrink-0" />
            )}
            {resumeFile ? (
              <span className="font-medium">{resumeFile.name}</span>
            ) : (
              <span>Click to upload your resume <span className="font-medium text-foreground">(.pdf)</span></span>
            )}
            <input
              id="resume"
              type="file"
              accept=".pdf,application/pdf"
              className="sr-only"
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null;
                setResumeFile(f);
                if (!f) setResume("");
              }}
            />
          </label>
        </div>
        <div>
          <Label htmlFor="gh">GitHub profile URL</Label>
          <input id="gh" className="field" value={github} onChange={(e) => setGithub(e.target.value)} placeholder="https://github.com/you" />
        </div>
        <div>
          <Label htmlFor="hours">Available hours per week</Label>
          <input id="hours" type="number" min={1} className="field" value={hours} onChange={(e) => setHours(Number(e.target.value))} />
        </div>
        <div>
          <Label htmlFor="langs" hint="Press Enter after each one">Spoken languages</Label>
          <TagInput id="langs" value={languages} onChange={setLanguages} placeholder="English, Hindi…" />
        </div>
        <div>
          <Label htmlFor="keys" hint="Which LLM APIs can you use? Never paste the keys themselves.">LLM API access</Label>
          <TagInput id="keys" value={keys} onChange={setKeys} placeholder="Gemini, OpenAI…" />
        </div>
        <div>
          <Label htmlFor="os" hint="Optional">Operating system</Label>
          <input id="os" className="field" value={os} onChange={(e) => setOs(e.target.value)} placeholder="macOS, Linux, Windows" />
        </div>
        <div>
          <Label>Willingness to learn</Label>
          <div className="flex flex-col gap-2">
            {LEARN.map((l) => (
              <label key={l} className="flex cursor-pointer items-center gap-2 text-sm">
                <input type="radio" name="learn" className="accent-lime" checked={learn === l} onChange={() => setLearn(l)} />
                {l}
              </label>
            ))}
          </div>
        </div>
        <div className="md:col-span-2">
          <div className="space-y-3">
            <Button
              onClick={onBuild}
              disabled={busy}
              aria-busy={busy}
              size="lg"
              variant={busy ? "secondary" : "default"}
              className="relative overflow-hidden disabled:opacity-100"
            >
              <span className="relative z-10 flex items-center gap-2">
                {busy && <Loader2 className="animate-spin" />}
                {parsing
                  ? "Reading PDF…"
                  : building
                    ? "Building your profile…"
                    : profile
                      ? "Rebuild my profile"
                      : "Build my profile"}
              </span>
              {busy && (
                <span className="absolute inset-x-0 bottom-0 h-[3px] overflow-hidden bg-night/60">
                  <span className="absolute inset-y-0 w-1/3 animate-slide rounded-full bg-lime" />
                </span>
              )}
            </Button>

            {busy && (
              <p role="status" className="font-mono text-xs text-muted-foreground">
                {parsing
                  ? "Extracting text from your PDF…"
                  : "Analysing your resume. This can take 10–30 seconds."}
              </p>
            )}
          </div>
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-6 rounded-xl border border-pink bg-night px-4 py-3 text-sm text-pink">
          {error}
        </p>
      )}

      {profile && (
        <>
          <ProfileReview profile={profile} onChange={setProfile} />
          <section className="panel mt-10 bg-violet-deep">
            <p className="eyebrow mb-4">Step 3 · Discover</p>
            <div className="mb-5 flex flex-wrap gap-2">
              {(
                [
                  ["search", "Plain-English search"],
                  ["profile", "Use my profile only"],
                ] as const
              ).map(([m, l]) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    "rounded-md px-4 py-2 text-sm font-semibold",
                    mode === m ? "bg-primary text-primary-foreground" : "bg-night text-foreground",
                  )}
                >
                  {l}
                </button>
              ))}
            </div>
            {mode === "search" ? (
              <input
                className="field mb-5"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="small Python bug fixes in LLM tooling I can finish in a weekend"
              />
            ) : (
              <p className="mb-5 text-sm text-muted-foreground">Nook will search using your skills, interests and hours alone.</p>
            )}
            <Button variant="lime" size="lg" onClick={onFind} disabled={finding}>
              {finding && <Loader2 className="animate-spin" />}
              {finding ? "Starting search…" : "Find issues"}
            </Button>
          </section>
        </>
      )}
    </div>
  );
}

function ProfileReview({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const set = (patch: Partial<Profile>) => onChange({ ...profile, ...patch });
  const updateSkill = (i: number, patch: Partial<Profile["skills"][number]>) =>
    set({ skills: profile.skills.map((s, j) => (j === i ? { ...s, ...patch } : s)) });

  return (
    <section className="mt-10">
      <p className="eyebrow mb-2">Step 2 · Review what Nook found</p>
      <h2 className="text-3xl font-medium">Your profile</h2>
      <p className="mt-2 text-sm text-muted-foreground">Edit anything that looks wrong before you continue.</p>

      <div className="mt-6 grid gap-6 md:grid-cols-3">
        {GROUPS.map((g) => {
          const items = profile.skills.map((s, i) => ({ s, i })).filter(({ s }) => s.level === g.level);
          return (
            <div key={g.level} className="panel p-5">
              <h3 className="mb-1 text-lg font-semibold">{g.label}</h3>
              {g.level === "inferred" && (
                <p className="mb-3 text-xs text-pink">
                  Inferred means mentioned but not demonstrated. Nook does not treat it as proven skill.
                </p>
              )}
              {items.length === 0 && <p className="text-sm text-muted-foreground">No skills in this group.</p>}
              <ul className="space-y-4">
                {items.map(({ s, i }) => (
                  <li key={i} className="space-y-2 border-b border-border pb-4 last:border-0">
                    <div className="flex gap-2">
                      <input className="field py-1 font-semibold" value={s.name} aria-label="Skill name" onChange={(e) => updateSkill(i, { name: e.target.value })} />
                      <button aria-label="Remove skill" className="text-muted-foreground hover:text-pink" onClick={() => set({ skills: profile.skills.filter((_, j) => j !== i) })}>
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                    <input className="field py-1 text-xs italic" value={s.evidence} aria-label="Evidence" onChange={(e) => updateSkill(i, { evidence: e.target.value })} />
                    <select
                      aria-label="Skill level"
                      className="w-full rounded-md bg-violet-deep px-3 py-1 text-xs"
                      value={s.level}
                      onChange={(e) => updateSkill(i, { level: e.target.value as SkillLevel })}
                    >
                      {GROUPS.map((x) => (
                        <option key={x.level} value={x.level}>{x.label}</option>
                      ))}
                    </select>
                  </li>
                ))}
              </ul>
              <Button
                variant="ghost"
                size="sm"
                className="mt-3"
                onClick={() => set({ skills: [...profile.skills, { name: "", level: g.level, evidence: "" }] })}
              >
                <Plus /> Add skill
              </Button>
            </div>
          );
        })}
      </div>

      <div className="panel mt-6 grid gap-6 md:grid-cols-2">
      </div>
    </section>
  );
}