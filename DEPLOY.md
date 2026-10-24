# Deploy Nook API — step-by-step

Target time: under 10 minutes.

---

## Option A — Render (recommended)

### 1. Push your repo to GitHub

```bash
git add .
git commit -m "chore: add render.yaml and Procfile"
git push
```

### 2. Create a new Web Service on Render

1. Go to <https://dashboard.render.com> → **New → Web Service**
2. Connect your GitHub repo.
3. Render auto-detects `render.yaml` — confirm the settings:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Health check path:** `/health`

### 3. Set environment variables

In **Environment → Environment Variables** add:

| Key | Value | Notes |
|-----|-------|-------|
| `TINYFISH_API_KEY` | `sk-…` | Required for live runs |
| `LLM_API_KEY` | `…` | Required for live runs |
| `LLM_PROVIDER` | `gemini` | or `openai` |
| `DEMO_MODE` | `false` | `true` skips all live calls |
| `DISCOVERY_FALLBACK_URLS` | _(optional)_ | Comma-separated URLs |
| `TINYFISH_TIMEOUT` | `120` | Seconds |
| `TINYFISH_MAX_CONCURRENT` | `3` | Lower to `1` on free tier |
| `LLM_TIMEOUT` | `30` | Seconds |

> Tip: on the free tier, set `TINYFISH_MAX_CONCURRENT=1` to avoid memory pressure.

### 4. Deploy

Click **Deploy** (or it triggers automatically on push). Watch the build log — it should finish in ~2 minutes.

### 5. Verify

```
curl https://<your-service>.onrender.com/health
# → {"status":"ok"}
```

---

## Option B — Railway

### 1. Push your repo to GitHub (same as above)

### 2. Create a new project on Railway

1. Go to <https://railway.app> → **New Project → Deploy from GitHub repo**
2. Select your repo.
3. Railway reads the `Procfile` automatically:
   ```
   web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```

### 3. Set environment variables

In the service settings → **Variables**, add the same keys listed in the Render table above.

### 4. Deploy

Railway deploys automatically on push. Check the build logs in the dashboard.

### 5. Verify

```
curl https://<your-service>.up.railway.app/health
# → {"status":"ok"}
```

---

## CORS

`app/main.py` uses `allow_origins=["*"]` so all origins are accepted out of the box.
When you're ready to lock it down, replace `"*"` with your frontend domain:

```python
allow_origins=["https://your-frontend.netlify.app"]
```

---

## SSE buffering

The SSE endpoint (`GET /jobs/{id}/stream`) already sets:

```
Cache-Control: no-cache
X-Accel-Buffering: no
```

`X-Accel-Buffering: no` prevents nginx-based proxies (used by both Render and Railway) from
buffering the stream. No extra host configuration is needed.

---

## Keep-warm before a demo

Render's free tier spins down after ~15 minutes of inactivity, causing a ~30 s cold start.

To avoid this, ping `/health` a few seconds before your demo:

```bash
# One-shot warm-up
curl https://<your-service>.onrender.com/health

# Or loop every 10 minutes while you prep
while true; do curl -s https://<your-service>.onrender.com/health; sleep 600; done
```

Alternatively, use a free cron service (e.g. <https://cron-job.org>) to hit `/health`
every 10 minutes during the demo window.

> `/health` returns `{"status":"ok"}` with no database or external calls — it responds in <5 ms.

---

## data/demo_results

The `data/demo_results/` directory is tracked via `data/demo_results/.gitkeep` and will be
present in the deployed container. Drop any pre-baked JSON demo files there before pushing.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| 502 on first request | Cold start | Warm up with `/health` first |
| SSE stream cuts out immediately | Proxy buffering | Confirm `X-Accel-Buffering: no` is in response headers |
| `TINYFISH_API_KEY` not found error | Env var not set | Check dashboard environment tab |
| Out-of-memory crash | Too many concurrent TinyFish runs | Set `TINYFISH_MAX_CONCURRENT=1` |
