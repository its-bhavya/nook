# TinyFish Agent API — Integration Notes

> Verified against https://tinyfish.mintlify.app/ on 24 Oct 2026.
> Re-check before the demo if anything looks wrong at runtime.

---

## Endpoints

| Pattern | URL | Returns | Cancellable |
|---------|-----|---------|-------------|
| Sync (blocking) | `POST https://agent.tinyfish.ai/v1/automation/run` | Final result | No |
| Async (poll) | `POST https://agent.tinyfish.ai/v1/automation/run-async` | `run_id` immediately | Yes |
| Streaming | `POST https://agent.tinyfish.ai/v1/automation/run-sse` | SSE event stream | Yes |
| Fetch run | `GET https://agent.tinyfish.ai/v1/runs/{id}` | Full run object | — |
| Cancel | `POST https://agent.tinyfish.ai/v1/runs/{id}/cancel` | Cancel status | — |

All three automation endpoints accept the **same JSON request body**.

---

## Authentication

- Header: `X-API-Key: <your key>`
- Key is obtained from the TinyFish dashboard.
- Store as `TINYFISH_API_KEY` environment variable — never in source code.
- 401 with code `MISSING_API_KEY` or `INVALID_API_KEY` on failure.

---

## Request body

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `url` | string | Yes | Target page URL |
| `goal` | string | Yes | Plain-English instruction |
| `output_schema` | object | No | JSON-schema subset for structured output (see below) |
| `browser_profile` | `lite` \| `stealth` | No | Defaults to `lite` |
| `agent_config.max_duration_seconds` | integer | No | Hard wall-clock timeout inside TinyFish |
| `agent_config.max_steps` | integer | No | 1–500, default 150. Beta-gated. |
| `proxy_config.*` | object | No | Optional proxy; `tinyfish` built-in or custom |
| `webhook_url` | string | No | HTTPS callback for lifecycle events |

---

## `output_schema` rules (important)

- Top level **must** be a JSON object (`type: "object"`).
- To return a list, put it in an array field — the top level cannot be an array.
- Nullable fields: use `nullable: true`, **not** `type: ["string", "null"]`.
- Unsupported keywords are rejected with a 400 before the run starts.
  - Banned: `oneOf`, `const`, `$ref`, `allOf`, `not`, boolean schema nodes (`true`/`false`).
  - Use `anyOf` inside fields only (not at top level).
- Max serialised schema size: 64 KB, max nesting depth: 10.
- When schema and goal disagree on shape, **schema wins**.

---

## Response shapes

### Sync (`/run`) — blocking until complete
```json
{
  "run_id": "...",
  "status": "COMPLETED | FAILED",
  "started_at": "ISO8601",
  "finished_at": "ISO8601",
  "num_of_steps": 12,
  "result": { /* your schema shape, or null */ },
  "error": null
}
```

### Async (`/run-async`) — immediate
```json
{ "run_id": "...", "error": null }
```
Then poll `GET /v1/runs/{id}` — statuses: `PENDING | RUNNING | COMPLETED | FAILED | CANCELLED`.

### Streaming (`/run-sse`) — SSE event stream
| Event type | Key fields |
|------------|------------|
| `STARTED` | `run_id`, `timestamp` |
| `STREAMING_URL` | `run_id`, `streaming_url` (live browser iframe, valid 24 h) |
| `PROGRESS` | `run_id`, `purpose` (human-readable step description) |
| `HEARTBEAT` | `timestamp` (keep-alive, no action needed) |
| `COMPLETE` | `run_id`, `status`, `result?`, `error?`, `timestamp` |

Each event is delivered as a standard SSE `data:` line containing JSON.

---

## Error object (on failure)

```json
{
  "code": "TASK_FAILED",
  "message": "...",
  "category": "SYSTEM_FAILURE | AGENT_FAILURE | BILLING_FAILURE | UNKNOWN",
  "retry_after": 5,
  "help_url": "...",
  "help_message": "..."
}
```

- `SYSTEM_FAILURE` → retry after `retry_after` seconds.
- `AGENT_FAILURE` → goal or URL needs fixing; **do not retry automatically**.
- `BILLING_FAILURE` → rejected before the run started; top up wallet, **do not retry**.
- `UNKNOWN` → treat as retryable.

Run-level failure codes: `TASK_FAILED`, `SITE_BLOCKED`, `MAX_STEPS_EXCEEDED`,
`TIMEOUT`, `BILLING_REJECTED`.

**Important:** a run can be `COMPLETED` with a `null` result or a `result.error` string
if the browser worked but the agent couldn't achieve the goal. Always check `result` content,
not just `status`.

---

## Pricing and usage

- Agent API draws from a pre-loaded wallet (pay-as-you-go). Minimum top-up: $10.
- Search and Fetch APIs are free within a daily allowance; overage draws from the wallet.
- No published per-run price found in the docs — check the dashboard for current rates.
- `BILLING_FAILURE` is returned (not a 402) when wallet is empty; plan is rejected before
  the browser starts, so no credits are consumed.

---

## Typical latency

- Not stated in official docs.
- Based on the step model (default 150 steps), a simple single-page extraction is likely
  **15–60 s**; a multi-page investigation could take **60–120 s**.
- Use `agent_config.max_duration_seconds` to bound wall-clock time inside TinyFish,
  and our own `timeout_s` as an outer client-side deadline.

---

## Rate limits

- No explicit Agent API rate limits found in the docs (only Search/Fetch have published limits).
- **⚠ Unclear** — contact TinyFish or test empirically. A 429 `RATE_LIMIT_EXCEEDED` is
  returned when hit.
- Nook limits concurrent TinyFish runs to `TINYFISH_MAX_CONCURRENT` (default 3) via an
  asyncio semaphore to stay well inside any undocumented limit.

---

## Flags / open questions

1. **Rate limits for Agent API** — not documented. Empirical test needed in first hour.
2. **GitHub bot detection** — TinyFish has a `stealth` browser profile option; may need
   `browser_profile: "stealth"` if `lite` gets blocked on GitHub pages.
3. **`max_steps` is beta-gated** — if our account is not in the beta, omitting the field
   uses the default of 150 steps.
4. **Wallet balance** — must be topped up before the demo. A `BILLING_FAILURE` stops the
   run silently from the user's perspective.
5. **`result` vs `result.result`** — docs note that a bare list or plain-text answer lands
   under `result.result`, while a JSON object appears directly in `result`. Using
   `output_schema` gives a consistent shape.
