"""
Run discover_issues + rank_candidates for the mock profile and print a
structured report: per-search counts, theme, score, matched_skills, reason,
and total elapsed time.

Usage (from repo root, TINYFISH_API_KEY + LLM_API_KEY must be set):
    python scripts/try_discover.py [--query "optional free-text query"]

Output:
    - printed to stdout
    - saved to evidence/discover_<timestamp>.json
"""
import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Enable structured logging so per-search table appears in stdout
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from app.discover import derive_themes, discover_issues
from app.mock_data import MOCK_PROFILE
from app.rank import rank_candidates

OUT_DIR = Path("evidence")

# Collect per-search rows for the summary table
_search_log: list[dict] = []


def on_event(msg: str) -> None:
    print(f"  >> {msg}")
    # Parse "Searched <theme>: N issues" lines into summary table
    if msg.startswith("Searched "):
        try:
            rest = msg[len("Searched "):]
            parts = rest.rsplit(":", 1)
            theme = parts[0].strip()
            count_str = parts[1].strip().split()[0] if len(parts) > 1 else "?"
            _search_log.append({"theme": theme, "count": count_str})
        except Exception:
            pass


async def main(query: str | None) -> None:
    print(f"Profile: {len(MOCK_PROFILE.skills)} skills, "
          f"{len(MOCK_PROFILE.interests)} interests")
    print(f"Query:   {query!r}\n")

    themes = derive_themes(MOCK_PROFILE, query)
    print(f"Derived themes ({len(themes)}):")
    for label, keywords in themes:
        print(f"  {label:30s}  keyword: {keywords}")
    print()

    # Discover
    t0 = time.monotonic()
    candidates = await discover_issues(MOCK_PROFILE, query, on_event=on_event)
    discover_elapsed = time.monotonic() - t0

    # Per-search summary table
    print(f"\n{'─'*60}")
    print(f"{'SEARCH':<35} {'RESULTS':>7}")
    print(f"{'─'*60}")
    for row in _search_log:
        print(f"  {row['theme']:<33} {row['count']:>7}")
    print(f"{'─'*60}")
    print(f"  Total candidates before ranking: {len(candidates)}")
    print(f"  Discovery elapsed: {discover_elapsed:.1f}s")
    print()

    # Theme counts breakdown
    theme_counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        theme_counts[c.theme or "generic"] += 1
    print("Candidates by theme:")
    for theme, count in sorted(theme_counts.items(), key=lambda x: -x[1]):
        print(f"  {theme:30s}  {count}")
    print()

    # Rank
    t1 = time.monotonic()
    shortlist = await rank_candidates(MOCK_PROFILE, candidates)
    rank_elapsed = time.monotonic() - t1
    total_elapsed = time.monotonic() - t0

    print(f"--- {len(shortlist)} ranked candidates (ranking took {rank_elapsed:.1f}s) ---\n")

    for i, c in enumerate(shortlist, 1):
        skills_str = ", ".join(c.matched_skills) if c.matched_skills else "(none)"
        labels_str = ", ".join(c.labels) if c.labels else "(none)"
        score_str = f"{c.score}/10" if c.score is not None else "—"
        print(f"{i}. {c.repo}")
        print(f"   title:          {c.title}")
        print(f"   url:            {c.url}")
        print(f"   theme:          {c.theme or '—'}")
        print(f"   score:          {score_str}")
        print(f"   matched_skills: {skills_str}")
        print(f"   reason:         {c.reason or '—'}")
        print(f"   labels:         {labels_str}")
        print(f"   comments:       {c.comments}   opened: {c.opened}   claim: {c.claimant_signal.value}")
        if c.difficulty:
            print(f"   difficulty:     {c.difficulty}")
        if c.domain:
            print(f"   domain:         {c.domain}")
        if c.tech_stack:
            print(f"   tech_stack:     {', '.join(c.tech_stack)}")
        print()

    final_themes = {c.theme or "generic" for c in shortlist}
    print(f"Theme coverage: {len(final_themes)} theme(s): {', '.join(sorted(final_themes))}")
    print(f"Total elapsed:  {total_elapsed:.1f}s")

    # Save
    OUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    out_file = OUT_DIR / f"discover_{timestamp}.json"
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query": query,
        "themes": [{"label": l, "keywords": k} for l, k in themes],
        "search_log": _search_log,
        "discover_elapsed_s": round(discover_elapsed, 2),
        "rank_elapsed_s": round(rank_elapsed, 2),
        "total_elapsed_s": round(total_elapsed, 2),
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
