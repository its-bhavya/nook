# Nook — Backend

> Find your place in open source. · HackIIITD, 24-25 Oct 2026

FastAPI backend for Nook. Discovers open-source issues via TinyFish and produces
personalised investigation reports.

## Requirements

- Python 3.11+

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and add your API keys
```

## Run

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at http://localhost:8000

Interactive docs: http://localhost:8000/docs

OpenAPI JSON (for the frontend tool): http://localhost:8000/openapi.json

## Demo mode

Set `DEMO_MODE=true` in `.env` to skip all live agent calls and serve saved results.

There are two layers of demo data:

**Mock data** (`mock=true` on `JobStatus`) — in-process data from `app/mock_data.py`. Available immediately with no pre-run step. Only the DSPy #9074 report (Persona A) is included. Any other issue URL returns an error in mock mode.

**Saved real-run results** (`from_cache=true` on `JobStatus`) — JSON files in `data/demo_results/` produced by `scripts/prerun.py`. These are real TinyFish runs saved ahead of the demo. They take precedence over mock data when `persona_id` is supplied.

### Generating saved results with prerun.py

```bash
# Run all demo issues for all personas and write to data/demo_results/
python scripts/prerun.py
```

File naming convention (set by the script, consumed by `app/demo_results.py`):
```
data/demo_results/investigate_<persona_id>_<url_slug>.json   # per-persona reports
data/demo_results/discover_<query_slug>.json                  # discovery results
```

Each file contains:
```json
{ "generated_at": "<ISO 8601>", "result": <InvestigationReport | CandidateIssue[]> }
```

The replay lookup key for `POST /investigate` is `(issue_url, persona_id)`. Pass `persona_id` in the request body to get the persona-specific saved report. Omit it to fall back to mock data (DSPy only) or a live run.

Re-run `scripts/prerun.py` on the morning of the demo to refresh results in case issue state has changed.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness check |
| POST | `/profile` | Resume text → structured Profile |
| POST | `/discover` | Profile + query → `job_id` |
| POST | `/investigate` | Issue URL + profile + optional `persona_id` → `job_id` |
| POST | `/investigate/batch` | Multiple issue URLs → list of `job_id`s |
| GET | `/jobs/{id}/stream` | SSE progress stream |
| GET | `/jobs/{id}` | Job status + result |
| GET | `/demo/inputs` | Personas, demo queries and demo issue URLs |

## Testing the mock flow

```bash
# 1. Health check
curl http://localhost:8000/health

# 2. Build a profile (returns mock profile)
curl -s -X POST http://localhost:8000/profile \
  -H "Content-Type: application/json" \
  -d '{"resume_text": "Python developer, FastAPI, RAG", "goals": "learn agent frameworks", "hours_per_week": 6, "spoken_languages": ["English"]}' | python -m json.tool

# 3. Start discovery
curl -s -X POST http://localhost:8000/discover \
  -H "Content-Type: application/json" \
  -d '{"profile": {"skills":[],"interests":["LLM"],"goals":"contribute","hours_per_week":5,"spoken_languages":["English"],"environment":{}}, "query": "LLM agent frameworks Python"}' | python -m json.tool
# Note the job_id

# 4. Stream progress (replace JOB_ID)
curl -N http://localhost:8000/jobs/JOB_ID/stream

# 5. Get final result
curl http://localhost:8000/jobs/JOB_ID | python -m json.tool

# 6. Start investigation
curl -s -X POST http://localhost:8000/investigate \
  -H "Content-Type: application/json" \
  -d '{"issue_url": "https://github.com/stanfordnlp/dspy/issues/9074", "profile": {"skills":[],"interests":[],"goals":"learn","hours_per_week":5,"spoken_languages":["English"],"environment":{}}}' | python -m json.tool
```

## Project structure

```
app/
  main.py        FastAPI app, CORS, routes
  config.py      Environment variable loading
  models.py      All Pydantic models
  jobs.py        In-memory job store
  mock_data.py   Realistic mock responses
requirements.txt
.env.example
```
