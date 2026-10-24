"""
End-to-end smoke test through the RUNNING Nook app.

Prerequisite: uvicorn serving app.main:app on http://127.0.0.1:8799
  .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8799

Flow:
  1. POST /discover  -> poll GET /jobs/{id} until done.
     Then read the job back from Neon Postgres (db.load_job) — NOT the app's
     in-memory dict — to prove the app persisted it.
  2. POST /investigate -> poll until done. The app uploads the report to Neon
     Object Storage; read the object back (storage.get_json) and confirm it
     matches the report returned by the API.

Run: .venv\\Scripts\\python.exe scripts\\smoke_app.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

import app.db as db
import app.storage as storage

BASE = os.environ.get("NOOK_BASE_URL", "http://127.0.0.1:8799")

MINIMAL_PROFILE = {
    "goals": "Find a small, well-scoped first open-source contribution",
    "hours_per_week": 6,
    "skills": [],
    "interests": ["LLM agents"],
    "spoken_languages": ["English"],
    "environment": {"has_llm_api_keys": [], "os": None},
}


def stamp(msg: str) -> None:
    print(f"[smoke] {msg}", flush=True)


async def run_job(client: httpx.AsyncClient, endpoint: str, payload: dict, label: str):
    r = await client.post(f"{BASE}{endpoint}", json=payload, timeout=60)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    stamp(f"{label}: created job {job_id}")
    for _ in range(60):  # up to ~30s
        await asyncio.sleep(0.5)
        jr = await client.get(f"{BASE}/jobs/{job_id}", timeout=30)
        jr.raise_for_status()
        data = jr.json()
        if data["status"] in ("done", "failed"):
            return job_id, data
    return job_id, {"status": "timeout", "result": None}


async def main() -> None:
    fails: list[str] = []

    async with httpx.AsyncClient(timeout=60) as client:
        # ---- Health / services ----
        hs = (await client.get(f"{BASE}/health/services", timeout=30)).json()
        stamp(f"services: postgres={hs['postgres']['ok']} storage={hs['object_storage']['ok']} "
              f"ai_gateway={hs['ai_gateway']['ok']} (configured={hs['ai_gateway']['configured']})")

        # ---- 1. Discover -> Postgres ----
        jid, data = await run_job(
            client, "/discover", {"profile": MINIMAL_PROFILE, "query": "good first issue python agents"}, "discover"
        )
        stamp(f"discover: status={data['status']} result_len={len(data.get('result') or [])}")
        if data["status"] != "done" or not data.get("result"):
            fails.append("discover did not complete with results")

        persisted = await db.load_job(jid)
        db_ok = (
            persisted is not None
            and persisted.job_id == jid
            and persisted.status.value == "done"
            and persisted.result == data.get("result")
        )
        stamp(f"postgres: job read back from DB = {db_ok}")
        if not db_ok:
            fails.append("postgres: job not persisted / mismatch")

        # ---- 2. Investigate -> Object Storage ----
        ijid, idata = await run_job(
            client,
            "/investigate",
            {"issue_url": "https://github.com/stanfordnlp/dspy/issues/9074", "profile": MINIMAL_PROFILE},
            "investigate",
        )
        result = idata.get("result") or {}
        storage_key = result.get("storage_key")
        stamp(f"investigate: status={idata['status']} storage_key={storage_key}")
        if idata["status"] != "done":
            fails.append("investigate did not complete")
        if not storage_key:
            fails.append("investigate: no storage_key (report not uploaded)")
        else:
            obj = await asyncio.to_thread(storage.get_json, storage_key)
            # The stored object is the report WITHOUT the storage_key field the API adds.
            expected = {k: v for k, v in result.items() if k != "storage_key" and k != "storage_error"}
            match = obj == expected
            stamp(f"storage: object read back matches report = {match}")
            if not match:
                fails.append("storage: uploaded object does not match report")

    await db.close_pool()
    stamp("== Summary ==")
    stamp(f"FAILS: {fails}" if fails else "ALL GOOD — Postgres + Object Storage end-to-end via the app")
    raise SystemExit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(main())
