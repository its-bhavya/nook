# Nook: Project Context & Technical Handout

Find your place in open source. · HackIIITD, 24-25 Oct 2026 · Hackathon build version, using TinyFish for discovery and investigation

> **Status key.** Everything below describes what we are building in 24 hours. Treat a component as implemented only once it is actually running. Items under "Roadmap" are not built.

## 1. Overview

Nook helps a developer decide whether a specific open-source issue is right for *them*. It does two things with a TinyFish web agent:

1. **Discovers** candidate issues from a plain-English request or from the developer's profile.
2. **Investigates** a shortlist by reading the context around each issue (discussion, linked pull requests, commits, documentation, claim signals) and producing a personalized, source-linked report.

The core idea: finding an issue is easy; knowing whether it is right for you is hard. Labels like `good first issue` say little about hidden blockers, prerequisites, who has already claimed the work, or whether maintainers are responsive.

**Tagline:** Find your place in open source. **Closing line:** Discover. Investigate. Contribute.

## 2. Problem

- **Misleading simplicity.** A beginner-friendly label can hide heavy setup, unfamiliar tooling, or a scope that is bigger than it looks.
- **Scattered information.** Requirements live across the issue thread, linked PRs, commits, discussions and docs.
- **Personal mismatch.** An issue can be easy in general but wrong for one person because of language, tools, accounts, time or interests.
- **Hidden state.** An issue can look open while a fix has already merged, a PR is already open, or someone has claimed it.
- **Unclear project activity.** Developers struggle to judge maintainer responsiveness, recent commits and PR flow.
- **Wasted time.** People discover blockers only after they have already invested hours.

Repository activity signals are useful context, but no single metric proves that a project is healthy or unhealthy.

## 3. How Nook differs from existing issue catalogs

Catalog sites (for example goodfirstissue-style pages) list beginner-friendly issues and attach a generated difficulty or "newbie friendliness" score based on the issue text. They are useful, and Nook can treat them as a source. Nook differs in what it answers.

|  | Catalog page | Nook |
| --- | --- | --- |
| Question answered | "Which issues exist and are tagged beginner-friendly?" | "Should *I* take this one?" |
| Personalization | One score for everyone | Uses the developer's skills, spoken languages, goals, hours and environment |
| Evidence | Summary of the issue text | Live read of linked PRs, comments, commits and docs, with source URLs |
| Claims and state | Shows a "possibly taken" flag | Interprets whether a claim looks active, stale, or superseded by a merged fix |
| Blockers | Generic research direction | Concrete, sourced blockers (language of the thread, required accounts or services, scope ambiguity) |
| Honesty about gaps | Numeric scores | Separates what sources say, what Nook infers, and what it could not verify |
| Output | A rating | A decision aid plus suggested first steps |

**Rule for claims:** only state a difference we have checked against the competitor's page for the same issue. Keep screenshots as proof.

## 4. Solution and core features

1. **Developer profile.** Resume text, optional GitHub profile URL, interests, goals, available hours, spoken languages, and willingness to learn, combined into one structured profile. Skills are tiered as `experienced`, `familiar` or `inferred`, so a technology merely mentioned on a resume is not treated as a demonstrated skill.
2. **Personalized discovery.** Natural-language search or profile-based discovery, run by TinyFish against GitHub issue-search pages and, if useful, aggregator pages.
3. **Investigation beyond labels.** TinyFish reads the issue and its surrounding context for each shortlisted candidate.
4. **Repository activity signals.** Observable signals only: recent commits, maintainer replies, releases, PR activity, with sources.
5. **Personalized report.** Fit reasoning, prerequisites, blockers, learning opportunities, evidence links, unknowns and suggested first steps.

## 5. Target users

- First-time contributors who need help with unfamiliar repositories.
- Busy developers with limited time.
- Experienced developers exploring new projects or domains.
- Learners looking for stretch tasks.

## 6. User journey

1. **Build a profile.** Paste resume text, add a GitHub URL and answer a few preference questions.
2. **Extract a structured profile.** An LLM turns the text into schema-validated fields.
3. **Choose a discovery mode.** Plain-English search or profile-based discovery.
4. **Find candidates.** TinyFish extracts issues from search pages into a structured list.
5. **Shortlist.** Nook validates and ranks candidates against the profile and keeps the top 3-5.
6. **Investigate.** TinyFish agents read the context around each shortlisted issue in parallel.
7. **Report.** The backend structures the findings and compares them with the profile.
8. **Decide.** The developer chooses whether to pursue the issue.

## 7. Screens (three for the hackathon)

| Screen | Purpose |
| --- | --- |
| Profile + Search | Collect profile and preferences; start discovery from text or profile |
| Results | Browse the shortlist, with live progress while discovery runs |
| Investigation Report | Live agent progress, then findings with three visibly separate blocks: **Sources say**, **Nook's interpretation**, **Unknowns** |

## 8. Architecture

```
Frontend (vibe-coded, React)
   ↓ REST + SSE
FastAPI backend
   ├─ Profile builder      (LLM, schema-constrained extraction)
   ├─ Discovery service    (LLM builds search URLs → TinyFish extracts candidates → validate → LLM ranks)
   ├─ Investigation service (TinyFish agent per shortlisted issue, run in parallel)
   ├─ Report generator     (LLM compares evidence bundle with profile)
   └─ Demo mode            (pre-run results served from saved JSON)
```

**Change from the original handout:** the GitHub REST API is removed. TinyFish handles both discovery and investigation. This showcases the agent's ability to use web pages the way a developer does, and keeps one external integration instead of two.

**Component responsibilities**

- **Frontend:** collects input, shows discovery and investigation progress, renders the report.
- **Backend:** orchestrates profile processing, TinyFish calls, evidence validation and profile matching. Holds all API keys.
- **TinyFish:** navigates accessible pages and returns structured findings.
- **Report generation:** converts findings into a consistent structure and separates sourced observations from interpretation and unknowns.

## 9. Technology stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Frontend | Generated with a vibe-coding tool (React-based) | Built against mock JSON first, then pointed at the real API |
| Backend | Python, FastAPI | Pydantic models define the API contract; CORS open for the hackathon |
| Web agent | TinyFish | Discovery extraction and issue investigation |
| LLM steps | Schema-constrained LLM calls | Profile extraction, query building, ranking, report writing |
| Live progress | Server-Sent Events | One stream per job |
| Jobs | In-memory dict of `job_id` to status and events | No queue or Redis |
| Deployment | Render or Railway for backend; Vercel or Netlify for frontend | Wake the backend before every demo |

Confirm the current TinyFish endpoint, authentication, request schema and streaming support against the official documentation before relying on any of the above.

## 10. API contract

| Endpoint | Purpose |
| --- | --- |
| `POST /profile` | Resume text, GitHub URL and preferences in; structured profile out |
| `POST /discover` | Profile and optional query in; returns a `job_id` |
| `POST /investigate` | Issue URL and profile in; returns a `job_id` |
| `GET /jobs/{id}/stream` | SSE progress events |
| `GET /jobs/{id}` | Final result |

The frontend tool should be given the FastAPI `/docs` page or the OpenAPI JSON so it does not invent fields.

## 11. Data shapes

**Profile**

```json
{
  "skills": [{"name": "FastAPI", "level": "experienced | familiar | inferred", "evidence": "short quote or project"}],
  "interests": ["LLM agents", "RAG"],
  "goals": "short text",
  "hours_per_week": 6,
  "spoken_languages": ["English", "Hindi"],
  "environment": {"has_llm_api_keys": ["Gemini"], "os": "optional"}
}
```

**Candidate issue (discovery output)**

```json
{
  "url": "https://github.com/<owner>/<repo>/issues/<n>",
  "repo": "owner/repo",
  "title": "...",
  "labels": ["good first issue"],
  "opened": "2026-10-22",
  "comments": 2,
  "claimant_signal": "none | claimed | linked_pr",
  "source": "github_search | aggregator"
}
```

The backend rejects any candidate whose URL does not match the GitHub issue pattern, removes duplicates, and drops anything not shown as open.

**Investigation report**

```json
{
  "issue_url": "...",
  "fit": {"level": "strong | possible | weak", "reasoning": "why, in plain words"},
  "sources_say": [{"claim": "...", "source_url": "..."}],
  "interpretation": ["Nook's inference, clearly marked as inference"],
  "prerequisites": ["..."],
  "blockers": [{"text": "...", "source_url": "... or null"}],
  "activity_signals": [{"signal": "...", "source_url": "..."}],
  "related_prs": [{"url": "...", "state": "open | merged | closed"}],
  "first_steps": ["..."],
  "unknowns": ["things the agent could not verify"]
}
```

The fit indicator comes with reasoning. We do not show a fake-precise numeric score.

## 12. TinyFish integration

**Discovery goal (example).** Given a GitHub issue-search URL, extract every listed open issue with title, repo, issue URL, labels, opened date, comment count, and any visible assignee, claim, or linked-PR indicator. Return only what is visible on the page and flag anything uncertain.

**Investigation goal (example).** Investigate this GitHub issue. Identify relevant pull requests, discussions, documentation, prerequisites, possible blockers, and repository activity signals. Return supporting source URLs and explicitly flag anything you cannot verify.

**Implementation rules**

- One shared wrapper, `run_tinyfish(goal, url, schema)`, used by both stages.
- Keep credentials on the server only.
- Use timeouts and controlled retries; never retry in a way that repeats costly runs without limit.
- Limit concurrent runs.
- Validate the returned structure, keep source URLs, and flag incomplete results.
- Test early for GitHub rate limits, bot checks and login walls. If discovery on GitHub pages is flaky, fall back to an aggregator page as the source.

## 13. Reliability and trust principles

- **Evidence versus inference.** Show what a source says separately from Nook's interpretation.
- **Source traceability.** Link important findings to URLs.
- **Explicit unknowns.** Report missing or inaccessible context instead of guessing.
- **Contextual signals.** Activity metrics inform the assessment; they do not produce definitive health labels.
- **Human decision-making.** Nook cannot guarantee an issue is easy, fits a schedule, or leads to an accepted contribution.
- **Credential security.** No API keys in frontend code or public repositories.
- **Privacy.** Collect only what is needed. Strip personal contact details from any resume used in a demo or committed to a repository.
- **No invented performance claims.** We report only what we measured.

## 14. Demo plan

**Persona A:** a Python developer strong in FastAPI, RAG, ChromaDB and DSPy, with some MLflow and Hugging Face, interested in agents and evaluation, with limited weekly hours (derived from a sanitized resume). **Persona B:** a second developer who differs on language, tools and API access, so the same issue produces a different verdict.

**Demo issue set.** Candidates found on 23 Oct 2026. Re-check each one the morning of the demo, because issues are claimed and fixed quickly during Hacktoberfest.

| Slot | Issue | What it shows |
| --- | --- | --- |
| Trap: already fixed | `stanfordnlp/dspy` #9074 | Looks like an open bug, but the thread says the fix already merged and the issue was never closed |
| Trap: PR exists | `cubrid-lab/cubrid-cookbook-python` #447 | Labeled beginner-friendly, but the index showed a linked PR |
| Trap: claimed | `supabase/supabase-py` #1806 to #1813 | One user claimed a block of these issues |
| Trap: personal mismatch | `confident-ai/deepeval` #3516 | Relevant topic, but the title is tagged TypeScript |
| Trap: hidden scope | `AstrBotDevs/AstrBot` #10720 | Rated small, but the thread is in Chinese and the author expects more schema fields to break after the first fix |
| Good fit | `pydantic/pydantic-ai` #11061 | LLM-API error handling, clear scope, fast maintainer replies |
| Good fit | `NVIDIA/garak` #2336 | Small, self-contained Python fix |
| Good fit | `vectorize-io/hindsight` #5981 | Agent-memory tooling close to Persona A's background; confirm language first |
| Stale or low-signal | `chroma-core/chroma` #2292, `ArduCAM/Arducam_tof_camera` #125 | Vague umbrella issue; weak maintainer signal. Verify their current state |

**Three-minute pitch**

1. Hook with a real story and one number from our interviews (20 s).
2. Live demo: plain-English search, the agent working, the same issue for two personas with different verdicts, and one trap with a cited source (90 s).
3. Why it is different: a catalog gives everyone the same score; Nook gives *you* a decision with sources and unknowns (30 s).
4. How TinyFish is used for discovery and investigation (20 s).
5. Close: Discover. Investigate. Contribute. (20 s)

**Q&A answer on catalogs.** Concede the overlap, then say Nook answers "should *I* take this?" by reading the surrounding PRs, comments and docs, comparing them with the person's profile, and showing sources and unknowns.

## 15. Scope for the hackathon

**In:** profile from pasted text, discovery through TinyFish, parallel investigation of 3-5 issues, report with the three separated blocks, live progress, demo mode with saved results, deployment.

**Out:** resume file parsing (PDF and DOCX), accounts and authentication, caching, queues, rate-limit management, cost dashboards.

**Rule:** if it does not appear in the demo, it does not get built.

## 16. Evaluation

Evaluate against real GitHub issues, never invented statistics.

| Area | What to check |
| --- | --- |
| Discovery relevance | Do results match the stated interests and constraints, and are they real, open issues? |
| Evidence coverage | Do key claims link to sources? |
| Accuracy | Are prerequisites, blockers and activity signals correct? Compare with hand-written reports for the demo set. |
| Uncertainty handling | Does the report expose what it could not verify? |
| Differentiation | Does the output differ across personas in a way a reader can see? |
| Operational feasibility | Latency, failure rate and cost per run, measured during the build |

## 17. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| TinyFish is slow or fails on stage | Demo mode serves saved real runs; start a run during the intro |
| GitHub blocks or rate-limits page reads | Test in the first hour; fall back to an aggregator source |
| Agent returns invented or malformed issues | Validate URLs and structure; drop anything unverifiable |
| Demo issues change state before judging | Re-check on Sunday morning and re-run saved results |
| Scope creep | Only build what appears in the demo |
| Backend cold start | Wake it before the demo and before judging |

## 18. Roadmap (not built)

- Caching and deduplication of investigation results, with freshness rules.
- Queued investigations, rate-limit handling and cost monitoring.
- Resume file parsing (PDF and DOCX).
- Per-user history and saved shortlists.
- Additional sources beyond GitHub pages.

These are planned strategies, not measured claims. Capacity and cost must be established by testing.

## 19. Team

- **Developer:** backend, TinyFish integration, LLM steps, deployment and integration.
- **Teammate (non-technical):** frontend generation with the vibe-coding tool, demo content and ground truth, QA of agent output, interviews, pitch, README and submission.

## 20. References

- TinyFish documentation (confirm the current endpoints and schema).
- GitHub issue-search pages and aggregator pages used as discovery sources.
- FastAPI documentation.
