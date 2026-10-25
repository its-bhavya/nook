# Nook

> Find your place in open source. · HackIIITD, 24–25 Oct 2026

Nook helps a developer decide whether a specific open-source issue is right for *them*. It discovers candidate issues matching your profile and investigates each one with a personalised, source-linked report — separating what sources actually say from what Nook infers and what it could not verify.

**Tagline:** Find your place in open source.  
**Stack:** Python · FastAPI · TinyFish · LLM (Gemini) · React frontend

---
## Architecture 
<img width="8192" height="6736" alt="GitHub Issue Discovery Pipeline-2026-10-25-051124" src="https://github.com/user-attachments/assets/2f0b4852-9b57-4229-8e11-2f96ea92b681" />

## How it works

1. **Profile** — paste your resume, optionally add a GitHub profile URL, and answer a few preference questions. An LLM extracts a structured profile with skills tiered as `experienced`, `familiar`, or `inferred`. GitHub signals are fetched in parallel and merged in: language counts, recent activity, external merged PRs, and repo topics.
2. **Discover** — give a plain-English query or let Nook build one from your profile. A TinyFish web agent extracts open issues from GitHub search pages, the backend validates and deduplicates them, then an LLM ranks the top candidates against your profile.
3. **Investigate** — for each shortlisted issue, a TinyFish agent reads the issue thread, linked PRs, commits, and documentation. The backend structures the findings into a personalised report with fit reasoning, prerequisites, blockers, activity signals, and suggested first steps.
4. **Decide** — review the report and choose whether to pursue the issue.

---

## Project structure

```
app/
  main.py             FastAPI app, routes, CORS, SSE
  config.py           Settings loaded from environment variables
  models.py           All Pydantic models (Profile, Skill, CandidateIssue, InvestigationReport …)
  profile.py          build_profile() — LLM extraction + GitHub enrichment
  github_profile.py   GitHub API enrichment (fetch_github_signals, merge_github_into_profile)
  ai_gateway.py       LLM integration (Neon AI Gateway or direct OpenAI-compatible)
  llm.py              generate_json() helper for schema-constrained LLM calls
  discover.py         Discovery pipeline (TinyFish → validate → rank)
  investigate.py      Investigation pipeline (TinyFish per issue)
  rank.py             LLM ranking of candidate issues
  report.py           Report generation from TinyFish evidence
  jobs.py             In-memory job store + async task runners
  tinyfish.py         TinyFish web agent wrapper (run_tinyfish)
  db.py               Neon Postgres integration (optional)
  storage.py          Neon Object Storage integration (optional)
  mock_data.py        In-process mock responses for demo mode
  demo_results.py     Loader for pre-run saved JSON results

data/
  personas.json        Demo personas (full profiles for the frontend)
  demo_queries.json    Pre-written demo search queries
  demo_issue_urls.json Curated demo issue URLs
  demo_results/        Pre-run investigation/discovery results (gitkeep'd)

scripts/
  try_github_profile.py   Diagnostic script for GitHub enrichment
  try_discover.py         Manual discovery test
  try_investigate.py      Manual investigation test
  try_report.py           Manual report generation test
  export_mocks.py         Export mock data to JSON
  smoke_tinyfish.py       TinyFish connectivity smoke test

Nook_frontend/            React frontend (separate README inside)
```

---

## Requirements

- Python 3.11+
- A TinyFish API key (for live discovery and investigation)
- An LLM API key — Gemini or any OpenAI-compatible provider (for profile extraction, ranking, and report writing)
- A GitHub personal access token (optional, but recommended — unauthenticated rate limit is 60 req/hour)

---

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and fill in your keys (see Environment variables below)
```

---

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs
- OpenAPI JSON: http://localhost:8000/openapi.json

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TINYFISH_API_KEY` | Yes (live) | — | TinyFish web agent key |
| `LLM_API_KEY` | Yes (live) | — | LLM provider key (Gemini or OpenAI-compatible) |
| `LLM_PROVIDER` | No | `gemini` | `gemini` or `openai` |
| `LLM_MODEL` | No | provider default | Model name override |
| `LLM_BASE_URL` | No | provider default | OpenAI-compatible endpoint override |
| `GITHUB_TOKEN` | No | — | GitHub personal access token — enables profile enrichment and avoids rate limits |
| `DEMO_MODE` | No | `false` | `true` bypasses all live calls and serves saved results |
| `DISCOVERY_FALLBACK_URLS` | No | — | Comma-separated fallback discovery URLs |
| `TINYFISH_TIMEOUT` | No | `120` | Max seconds per TinyFish run (legacy) |
| `INVESTIGATE_TIMEOUT_S` | No | `240` | Max seconds for an investigation run |
| `DISCOVER_TIMEOUT_S` | No | `270` | Max seconds for a discovery run |
| `TINYFISH_MAX_CONCURRENT` | No | `4` | Max parallel TinyFish runs (set to `1` on free-tier hosts) |
| `LLM_TIMEOUT` | No | `120` | Max seconds to wait for a single LLM response |
| `DATABASE_URL` | No | — | Neon Postgres pooled connection string (optional persistence) |
| `AWS_ACCESS_KEY_ID` etc. | No | — | Neon Object Storage credentials (optional report archiving) |

Copy `.env.example` to `.env` and fill in the values. Never commit `.env`.

---

## GitHub profile enrichment

When a GitHub profile URL is provided on `POST /profile`, Nook fetches four GitHub API endpoints in parallel:

- `/users/{u}` — bio, followers, account created date
- `/users/{u}/repos` — own non-fork repos, languages, topics, stars
- `/search/issues` — merged PRs to repos the developer does not own
- `/users/{u}/events/public` — activity in the last 90 days

It then:

1. Counts languages across non-fork repos and identifies ones pushed in the last 365 days.
2. Derives skill levels: **experienced** if ≥3 own repos, or ≥1 own repo + recent activity + external merged PRs; **familiar** if at least one repo was pushed in the last year.
3. Merges GitHub-derived skills into the LLM-extracted profile — upgrading level and replacing evidence when GitHub signals are stronger, or appending evidence when they are equal or weaker.
4. Adds up to 5 repo topics to `profile.interests` (no duplicates).

Any failure (bad token, rate limit, network error) is silently ignored and the resume-only profile is used.

To test enrichment in isolation:

```bash
python -m scripts.try_github_profile https://github.com/your-username
```

---

## API reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/health/services` | Status of Postgres, Object Storage, and LLM |
| `POST` | `/parse-resume` | Upload a PDF and get plain text back |
| `POST` | `/profile` | Resume text + optional GitHub URL → structured `Profile` |
| `POST` | `/discover` | `Profile` + optional query → `job_id` |
| `POST` | `/investigate` | Issue URL + `Profile` + optional `persona_id` → `job_id` |
| `POST` | `/investigate/batch` | Multiple issue URLs → list of `job_id`s |
| `GET` | `/jobs/{id}/stream` | SSE progress stream (replays past events, then live) |
| `GET` | `/jobs/{id}` | Job status + result (falls back to Postgres on restart) |
| `GET` | `/demo/inputs` | Personas, demo queries, and demo issue URLs |

### Profile request

```json
{
  "resume_text": "...",
  "github_url": "https://github.com/you",
  "interests": ["LLM agents", "RAG"],
  "goals": "learn agent frameworks",
  "hours_per_week": 6,
  "spoken_languages": ["English"],
  "environment": { "has_llm_api_keys": ["Gemini"], "os": "macOS" }
}
```

### Profile response

```json
{
  "skills": [
    { "name": "FastAPI", "level": "experienced", "evidence": "built REST APIs at Acme" },
    { "name": "Python", "level": "experienced", "evidence": "GitHub: 12 own repos in Python (4 active in the last year), plus 3 merged PRs to other repos" }
  ],
  "interests": ["LLM agents", "RAG", "fastapi", "machine-learning"],
  "goals": "learn agent frameworks",
  "hours_per_week": 6,
  "spoken_languages": ["English"],
  "environment": { "has_llm_api_keys": ["Gemini"], "os": "macOS" }
}
```

Skill `evidence` strings prefixed with `"GitHub:"` come from the GitHub enrichment step.

### Job lifecycle

Jobs move through `queued → running → done | failed`. Poll `GET /jobs/{id}` or stream `GET /jobs/{id}/stream` (SSE). The stream replays all past events on connect, then delivers new ones as they arrive, and closes with a `done` or `failed` event.

---

## LLM backends

`POST /profile` tries backends in order:

1. **Neon AI Gateway** — when `NEON_AI_GATEWAY_TOKEN` and `NEON_AI_GATEWAY_BASE_URL` are set. GitHub enrichment is applied after the LLM call.
2. **Direct LLM** — when `LLM_API_KEY` is set. GitHub enrichment runs concurrently with the LLM call inside `build_profile()`.
3. **Mock fallback** — returns `MOCK_PROFILE` from `app/mock_data.py` with no external calls.

---

## Demo mode

Set `DEMO_MODE=true` to bypass all live agent calls. Two layers of demo data are available:

**In-process mock** (`mock: true` on `JobStatus`) — data from `app/mock_data.py`. Available immediately. Only the DSPy #9074 report (Persona A) is included; other issue URLs return an error.

**Saved real-run results** (`from_cache: true` on `JobStatus`) — JSON files in `data/demo_results/` produced by running `scripts/prerun.py` against the live APIs ahead of time. These take precedence over in-process mock when `persona_id` is supplied.

```bash
# Generate saved results for all demo personas and issues
python scripts/prerun.py
```

File naming:
```
data/demo_results/investigate_<persona_id>_<url_slug>.json
data/demo_results/discover_<query_slug>.json
```

Re-run `scripts/prerun.py` on the morning of the demo to refresh results in case issue state has changed.

---

## Quick smoke test

```bash
# Health check
curl http://localhost:8000/health

# Build a profile with GitHub enrichment
curl -s -X POST http://localhost:8000/profile \
  -H "Content-Type: application/json" \
  -d '{
    "resume_text": "Python developer, FastAPI, RAG pipelines",
    "github_url": "https://github.com/your-username",
    "goals": "learn agent frameworks",
    "hours_per_week": 6,
    "spoken_languages": ["English"]
  }' | python -m json.tool

# Start discovery (note the job_id)
curl -s -X POST http://localhost:8000/discover \
  -H "Content-Type: application/json" \
  -d '{
    "profile": {"skills":[],"interests":["LLM"],"goals":"contribute","hours_per_week":5,"spoken_languages":["English"],"environment":{}},
    "query": "LLM agent frameworks Python"
  }' | python -m json.tool

# Stream progress (replace JOB_ID)
curl -N http://localhost:8000/jobs/JOB_ID/stream

# Get final result
curl http://localhost:8000/jobs/JOB_ID | python -m json.tool

# Start investigation
curl -s -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d '{
    "issue_url": "https://github.com/stanfordnlp/dspy/issues/9074",
    "profile": {"skills":[],"interests":[],"goals":"learn","hours_per_week":5,"spoken_languages":["English"],"environment":{}}
  }' | python -m json.tool
```

---

## Deployment

See [DEPLOY.md](DEPLOY.md) for step-by-step instructions for Render and Railway.

Key points:
- The `Procfile` and `render.yaml` are already configured.
- Set all required environment variables in the host dashboard — never in the repo.
- On Render's free tier, set `TINYFISH_MAX_CONCURRENT=1` to avoid memory pressure.
- Ping `/health` before a demo to avoid cold-start delays (~30 s on the free tier).
