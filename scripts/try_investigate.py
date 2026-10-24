"""
Run investigate_issue on DSPy #9074, print the EvidenceBundle, and save to JSON.

Usage (from repo root, TINYFISH_API_KEY must be set):
    python scripts/try_investigate.py

Output:
    - printed to stdout
    - saved to evidence/dspy_9074.json
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.investigate import investigate_issue

ISSUE_URL = "https://github.com/stanfordnlp/dspy/issues/9074"
OUT_DIR = Path("evidence")


def on_event(msg: str) -> None:
    print(f"  >> {msg}")


async def main() -> None:
    print(f"Investigating: {ISSUE_URL}\n")
    bundle = await investigate_issue(ISSUE_URL, on_event=on_event)

    print("\n--- EvidenceBundle ---")
    bundle_dict = bundle.model_dump()
    print(json.dumps(bundle_dict, indent=2))

    print("\n--- Summary ---")
    print(f"  partial        : {bundle.partial}")
    print(f"  already_resolved: {bundle.already_resolved}  ({bundle.already_resolved_url})")
    print(f"  findings       : {len(bundle.findings)}")
    print(f"  related_prs    : {len(bundle.related_prs)}")
    print(f"  claim_signals  : {len(bundle.claim_signals)}")
    print(f"  activity_signals: {len(bundle.activity_signals)}")
    print(f"  unknowns       : {len(bundle.unknowns)}")
    print(f"  elapsed        : {bundle.elapsed_s:.1f}s")

    # Save to file
    OUT_DIR.mkdir(exist_ok=True)
    out_file = OUT_DIR / "dspy_9074.json"
    
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_url": ISSUE_URL,
        "bundle": bundle_dict,
    }
    
    out_file.write_text(json.dumps(payload, indent=2))
    print(f"\n--- Saved ---")
    print(f"  {out_file} ({out_file.stat().st_size} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
