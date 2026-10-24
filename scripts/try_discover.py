"""
Run discover_issues for a sample query and print the validated candidates.

Usage (from repo root, TINYFISH_API_KEY must be set):
    python scripts/try_discover.py

Output:
    - printed to stdout
    - saved to evidence/discover_<timestamp>.json
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.discover import discover_issues
from app.mock_data import MOCK_PROFILE

QUERY = "python issues related to LLM agents or RAG"
OUT_DIR = Path("evidence")


def on_event(msg: str) -> None:
    print(f"  >> {msg}")


async def main() -> None:
    print(f"Query: {QUERY!r}\n")

    candidates = await discover_issues(MOCK_PROFILE, QUERY, on_event=on_event)

    print(f"\n--- {len(candidates)} validated candidates ---\n")
    for i, c in enumerate(candidates, 1):
        labels = ", ".join(c.labels) if c.labels else "(none)"
        print(f"{i:2}. [{c.source.value}] {c.repo}")
        print(f"    {c.title}")
        print(f"    {c.url}")
        print(f"    labels={labels}  comments={c.comments}  opened={c.opened}  claim={c.claimant_signal.value}")
        print()

    # Save to file
    OUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_file = OUT_DIR / f"discover_{timestamp}.json"

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": QUERY,
        "candidates": [c.model_dump() for c in candidates],
    }
    out_file.write_text(json.dumps(payload, indent=2))
    print(f"Saved to {out_file} ({out_file.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
