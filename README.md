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

Set `DEMO_MODE=true` in `.env` to skip all live agent calls and return
realistic saved results. Useful when presenting or when API keys are not
available.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness check |
| POST | `/profile` | Resume text → structured Profile |
| POST | `/discover` | Profile + query → `job_id` |
| POST | `/investigate` | Issue URL + profile → `job_id` |
| GET | `/jobs/{id}/stream` | SSE progress stream |
| GET | `/jobs/{id}` | Job status + result |

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
