"""
Export mock data to JSON files for the frontend teammate.
No server required — imports mock data directly.

Usage:
    python scripts/export_mocks.py

Output:
    mock/profile.json
    mock/discover_job.json
    mock/investigate_job.json
    mock/progress_events.json
"""
import json
import sys
import uuid
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.mock_data import (
    DISCOVER_PROGRESS_STEPS,
    INVESTIGATE_PROGRESS_STEPS,
    MOCK_CANDIDATES,
    MOCK_INVESTIGATION,
    MOCK_PROFILE,
)
from app.models import JobKind, JobStatusEnum

OUT = Path("mock")
OUT.mkdir(exist_ok=True)

JOB_ID_DISCOVER   = "demo-discover-000-0000-000000000001"
JOB_ID_INVESTIGATE = "demo-investigate-000-0000-000000000002"


def make_events(steps: list[str], base_ts: str = "2026-10-24T10:00:00+00:00") -> list[dict]:
    """Turn progress step strings into JobEvent-shaped dicts with sequential timestamps."""
    from datetime import datetime, timedelta, timezone
    t = datetime.fromisoformat(base_ts)
    events = []
    for step in steps:
        events.append({"message": step, "timestamp": t.isoformat()})
        t += timedelta(seconds=1)
    return events


def write(path: Path, data: dict | list) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"  wrote {path}  ({path.stat().st_size} bytes)")


# 1. profile.json
write(OUT / "profile.json", MOCK_PROFILE.model_dump())

# 2. discover_job.json — a completed discovery job with candidate list as result
discover_job = {
    "job_id": JOB_ID_DISCOVER,
    "kind": JobKind.discover.value,
    "status": JobStatusEnum.done.value,
    "events": make_events(DISCOVER_PROGRESS_STEPS),
    "result": [c.model_dump() for c in MOCK_CANDIDATES],
}
write(OUT / "discover_job.json", discover_job)

# 3. investigate_job.json — a completed investigation job with full report as result
investigate_job = {
    "job_id": JOB_ID_INVESTIGATE,
    "kind": JobKind.investigate.value,
    "status": JobStatusEnum.done.value,
    "events": make_events(INVESTIGATE_PROGRESS_STEPS),
    "result": MOCK_INVESTIGATION.model_dump(),
}
write(OUT / "investigate_job.json", investigate_job)

# 4. progress_events.json — both SSE sequences side by side for easy reference
progress_events = {
    "discover": [
        {"type": "progress", "message": e["message"], "timestamp": e["timestamp"]}
        for e in make_events(DISCOVER_PROGRESS_STEPS)
    ]
    + [{"type": "done", "message": "Job completed", "result": discover_job["result"]}],
    "investigate": [
        {"type": "progress", "message": e["message"], "timestamp": e["timestamp"]}
        for e in make_events(INVESTIGATE_PROGRESS_STEPS)
    ]
    + [{"type": "done", "message": "Job completed", "result": investigate_job["result"]}],
}
write(OUT / "progress_events.json", progress_events)

print("\nDone. Send the mock/ folder to your teammate.")
