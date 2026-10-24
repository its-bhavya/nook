"""
Run investigate_issue on three GitHub issues and print per-issue metrics.

Usage (from repo root, TINYFISH_API_KEY + LLM_API_KEY must be set):
    python scripts/try_investigate.py

Prints for each issue:
  - path used  (fetch | fetch+agent | agent)
  - elapsed seconds
  - number of findings
  - whether the report was generated

Also saves each evidence bundle to evidence/<slug>.json.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.investigate import investigate_issue

ISSUES = [
    # Trap: already fixed — issue page + PR text should be enough via fast path
    ("dspy_9074",  "https://github.com/stanfordnlp/dspy/issues/9074"),
    # Good fit: clear scope, fast maintainer replies
    ("pydantic_ai_11061", "https://github.com/pydantic/pydantic-ai/issues/11061"),
    # Trap: hidden scope / language mismatch
    ("astrbot_10720", "https://github.com/AstrBotDevs/AstrBot/issues/10720"),
]

OUT_DIR = Path("evidence")
OUT_DIR.mkdir(exist_ok=True)


def on_event(label: str, slug: str) -> None:
    print(f"  [{slug}] >> {label}")


async def run_one(slug: str, url: str) -> dict:
    print(f"\n{'='*60}")
    print(f"Investigating: {url}")

    bundle = await investigate_issue(url, on_event=lambda msg: on_event(msg, slug))

    report_generated = len(bundle.findings) >= 1 and not (
        bundle.partial and len(bundle.findings) == 0
    )

    result = {
        "slug":             slug,
        "url":              url,
        "path_used":        bundle.path_used,
        "elapsed_s":        round(bundle.elapsed_s, 1),
        "findings":         len(bundle.findings),
        "related_prs":      len(bundle.related_prs),
        "activity_signals": len(bundle.activity_signals),
        "partial":          bundle.partial,
        "already_resolved": bundle.already_resolved,
        "report_generated": report_generated,
        "unknowns":         bundle.unknowns[:3],
    }

    # Save bundle
    out_file = OUT_DIR / f"{slug}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue_url": url,
        "bundle": bundle.model_dump(),
    }
    out_file.write_text(json.dumps(payload, indent=2))

    return result


async def main() -> None:
    all_results: list[dict] = []
    for slug, url in ISSUES:
        r = await run_one(slug, url)
        all_results.append(r)

    # Summary table
    print(f"\n\n{'='*60}")
    print(f"{'ISSUE':<22} {'PATH':<13} {'ELAPSED':>8} {'FINDINGS':>9} {'REPORT?':>8}")
    print("-" * 65)
    for r in all_results:
        report_flag = "YES" if r["report_generated"] else "NO (empty)"
        print(
            f"{r['slug']:<22} {r['path_used']:<13} "
            f"{r['elapsed_s']:>7.1f}s {r['findings']:>9} {report_flag:>8}"
        )
    print("=" * 65)

    # Detailed per-issue notes
    for r in all_results:
        print(f"\n[{r['slug']}]")
        print(f"  already_resolved : {r['already_resolved']}")
        print(f"  partial          : {r['partial']}")
        print(f"  related_prs      : {r['related_prs']}")
        print(f"  activity_signals : {r['activity_signals']}")
        if r["unknowns"]:
            for u in r["unknowns"]:
                print(f"  unknown          : {u}")

    print(f"\nBundles saved to {OUT_DIR}/")


if __name__ == "__main__":
    asyncio.run(main())
