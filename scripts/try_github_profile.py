"""Diagnostic script for GitHub profile enrichment."""
import asyncio
import sys

import httpx

from app.config import get_settings
from app.github_profile import fetch_github_signals, parse_username, skills_from_signals


async def main():
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    if not url:
        print("Usage: python -m scripts.try_github_profile https://github.com/its-bhavya")
        sys.exit(1)

    # 1. URL parsing
    username = parse_username(url)
    if not username:
        print(f"ERROR: Could not parse username from URL: {url!r}")
        print("  URL must match: https://github.com/<username>")
        sys.exit(1)
    print(f"Username parsed: {username}")

    # 2. Token check
    token = get_settings().github_token
    if token:
        print(f"GitHub token: set (****{token[-4:]})")
    else:
        print("GitHub token: NOT SET — using unauthenticated (60 req/hour limit)")

    # 3. Raw /users call to see the actual error
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    print(f"\nTesting https://api.github.com/users/{username} ...")
    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        resp = await client.get(f"https://api.github.com/users/{username}")
    print(f"  Status: {resp.status_code}")
    if resp.status_code != 200:
        print(f"  Body: {resp.text[:300]}")
        sys.exit(1)

    # 4. Full fetch
    print("\nRunning fetch_github_signals ...")
    sig = await fetch_github_signals(url)
    if sig is None:
        print("ERROR: fetch_github_signals returned None (check logs above)")
        sys.exit(1)

    print(f"  bio: {sig['bio']!r}")
    print(f"  followers: {sig['followers']}")
    print(f"  language_counts: {sig['language_counts']}")
    print(f"  recent_language_counts: {sig['recent_language_counts']}")
    print(f"  top_topics: {sig['top_topics']}")
    print(f"  top_repos: {sig['top_repos']}")
    print(f"  external_merged_pr_count: {sig['external_merged_pr_count']}")
    print(f"  event_count_90d: {sig['event_count_90d']}")

    skills = skills_from_signals(sig)
    print(f"\nDerived skills ({len(skills)}):")
    for s in skills:
        print(f"  {s.name} [{s.level.value}] — {s.evidence}")


asyncio.run(main())
