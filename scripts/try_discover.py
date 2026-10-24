"""
Run discover_issues + rank_candidates for the mock profile and print a
structured report: theme, matched_skills, reason per candidate, plus counts.

Usage (from repo root, TINYFISH_API_KEY + LLM_API_KEY must be set):
    python scripts/try_discover.py [--query "optional free-text query"]

Output:
    - printed to stdout
    - saved to evidence/discover_<timestamp>.json
"""
import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.discover import derive_themes, discover_issues
from app.mock_data import MOCK_PROFILE
from app.rank import rank_candidates

OUT_DIR = Path("evidence")


def on_event(msg: str) -> None:
    print(f"  >> {msg}")


async def main(query: str | None) -> None:
    print(f"Profile: {len(MOCK_PROFILE.skills)} skills, "
          f"{len(MOCK_PROFILE.interests)} interests")
    print(f"Query:   {query!r}\n")

    # Show derived themes before hitting the network
    themes = derive_themes(MOCK_PROFILE, query)
    print(f"Derived themes ({len(themes)}):")
    for label, keywords in themes:
        print(f"  {label:25s}  keywords: {keywords}")
    print()

    # Discover
    candidates = await discover_issues(MOCK_PROFILE, query, on_event=on_event)
    print(f"\n--- {len(candidates)} candidates before ranking ---\n")

    # Theme counts
    theme_counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        theme_counts[c.theme or "generic"] += 1
    print("Candidates by theme:")
    for theme, count in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme:25s}  {count}")
    print()

    # Rank
    shortlist = await rank_candidates(MOCK_PROFILE, candidates)
    print(f"--- {len(shortlist)} ranked candidates ---\n")

    for i, c in enumerate(shortlist, 1):
        skills_str = ", ".join(c.matched_skills) if c.matched_skills else "(none)"
        labels_str = ", ".join(c.labels) if c.labels else "(none)"
        print(f"{i}. {c.repo}")
        print(f"   title:          {c.title}")
        print(f"   url:            {c.url}")
        print(f"   theme:          {c.theme or '—'}")
        print(f"   matched_skills: {skills_str}")
        print(f"   reason:         {c.reason or '—'}")
        print(f"   labels:         {labels_str}")
        print(f"   comments:       {c.comments}   opened: {c.opened}   claim: {c.claimant_signal.value}")
        print()

    # Theme coverage of final shortlist
    final_themes = {c.theme or "generic" for c in shortlist}
    print(f"Theme coverage in shortlist: {len(final_themes)} theme(s): {', '.join(sorted(final_themes))}")

    # Save
    OUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_file = OUT_DIR / f"discover_{timestamp}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "themes": [{"label": l, "keywords": k} for l, k in themes],
        "candidates_before_ranking": len(candidates),
        "candidates": [c.model_dump() for c in candidates],
        "shortlist": [c.model_dump() for c in shortlist],
    }
    out_file.write_text(json.dumps(payload, indent=2))
    print(f"\nSaved to {out_file} ({out_file.stat().st_size} bytes)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=None, help="Optional free-text query")
    args = parser.parse_args()
    asyncio.run(main(args.query))
