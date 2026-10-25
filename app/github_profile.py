"""
GitHub profile enrichment for Nook.

parse_username(url)          – extract GitHub username from a profile URL
fetch_github_signals(url)    – async; queries GitHub API; returns dict or None
skills_from_signals(sig)     – derive Skill objects from signal dict
merge_github_into_profile    – merge GitHub skills + topics into a Profile
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import get_settings
from app.models import Profile, Skill, SkillLevel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GH_USERNAME_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_\-]+)/?$"
)


def parse_username(url: str) -> str | None:
    """Return the GitHub username from a github.com profile URL, or None."""
    m = _GH_USERNAME_RE.match(url.strip())
    return m.group(1) if m else None


def _make_headers() -> dict[str, str]:
    token = get_settings().github_token
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


# ---------------------------------------------------------------------------
# Signal fetching
# ---------------------------------------------------------------------------

async def fetch_github_signals(url: str) -> dict | None:
    """
    Fetch GitHub signals for the profile URL.  Returns None on any failure of
    the /users/{u} call or if the username cannot be parsed.
    """
    username = parse_username(url)
    if not username:
        return None

    headers = _make_headers()
    base = "https://api.github.com"

    async with httpx.AsyncClient(headers=headers, timeout=20) as client:
        # Fire four requests in parallel
        user_url     = f"{base}/users/{username}"
        repos_url    = f"{base}/users/{username}/repos?per_page=100&sort=pushed&type=owner"
        prs_url      = (
            f"{base}/search/issues"
            f"?q=author:{username}+type:pr+is:merged+-user:{username}&per_page=30"
        )
        events_url   = f"{base}/users/{username}/events/public?per_page=100"

        try:
            user_resp, repos_resp, prs_resp, events_resp = await asyncio.gather(
                client.get(user_url),
                client.get(repos_url),
                client.get(prs_url),
                client.get(events_url),
            )
        except Exception:
            return None

        # /users/{u} must succeed
        if user_resp.status_code != 200:
            return None

        user_data = user_resp.json()

    # -----------------------------------------------------------------------
    # Repos (non-fork only)
    # -----------------------------------------------------------------------
    repos: list[dict] = []
    if repos_resp.status_code == 200:
        repos = [r for r in repos_resp.json() if not r.get("fork", False)]

    now = datetime.now(timezone.utc)
    cutoff_1y = now.replace(year=now.year - 1)
    cutoff_90d = now.timestamp() - 90 * 86400

    lang_counts: dict[str, int] = {}
    recent_lang_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}

    top5_repos: list[dict] = []

    for repo in repos:
        lang = repo.get("language") or ""
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        # Recent = pushed in last 365 days
        pushed_at = repo.get("pushed_at") or ""
        if pushed_at:
            try:
                pushed_dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
                if pushed_dt >= cutoff_1y:
                    if lang:
                        recent_lang_counts[lang] = recent_lang_counts.get(lang, 0) + 1
            except ValueError:
                pass

        # Topics
        for topic in repo.get("topics") or []:
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

    # Top repos by stars (up to 5)
    starred = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)
    top5_repos = [
        {"name": r["full_name"], "stars": r.get("stargazers_count", 0)}
        for r in starred[:5]
    ]

    # Top topics (up to 10)
    top_topics = sorted(topic_counts, key=lambda t: topic_counts[t], reverse=True)[:10]

    # -----------------------------------------------------------------------
    # Merged external PRs
    # -----------------------------------------------------------------------
    external_pr_count = 0
    external_pr_repos: list[str] = []
    if prs_resp.status_code == 200:
        items = prs_resp.json().get("items", [])
        external_pr_count = len(items)
        seen: set[str] = set()
        for item in items:
            repo_url = item.get("repository_url", "")
            repo_slug = "/".join(repo_url.split("/")[-2:]) if repo_url else ""
            if repo_slug and repo_slug not in seen:
                seen.add(repo_slug)
                external_pr_repos.append(repo_slug)

    # -----------------------------------------------------------------------
    # Events in last 90 days
    # -----------------------------------------------------------------------
    event_count_90d = 0
    if events_resp.status_code == 200:
        for event in events_resp.json():
            created = event.get("created_at") or ""
            if created:
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff_90d:
                        event_count_90d += 1
                except ValueError:
                    pass

    return {
        "username": username,
        "bio": user_data.get("bio") or "",
        "followers": user_data.get("followers", 0),
        "account_created": user_data.get("created_at") or "",
        "language_counts": lang_counts,
        "recent_language_counts": recent_lang_counts,
        "top_topics": top_topics,
        "top_repos": top5_repos,
        "external_merged_pr_count": external_pr_count,
        "external_merged_pr_repos": external_pr_repos,
        "event_count_90d": event_count_90d,
    }


# ---------------------------------------------------------------------------
# Skill derivation
# ---------------------------------------------------------------------------

def skills_from_signals(sig: dict) -> list[Skill]:
    """
    Derive Skill objects from GitHub signal dict.

    Level rules per language:
      experienced – >=3 own repos  OR  (>=1 own repo AND a recent repo AND external merged PRs)
      familiar    – at least one repo pushed in the last year
      (skip otherwise)
    """
    lang_counts: dict[str, int] = sig.get("language_counts", {})
    recent_counts: dict[str, int] = sig.get("recent_language_counts", {})
    external_pr_count: int = sig.get("external_merged_pr_count", 0)
    skills: list[Skill] = []

    for lang, total in lang_counts.items():
        recent = recent_counts.get(lang, 0)
        has_external_prs = external_pr_count > 0

        if total >= 3 or (total >= 1 and recent >= 1 and has_external_prs):
            level = SkillLevel.experienced
            parts = [f"GitHub: {total} own repo{'s' if total != 1 else ''} in {lang}"]
            if recent:
                parts.append(f"{recent} active in the last year")
            if has_external_prs:
                parts.append(f"plus {external_pr_count} merged PR{'s' if external_pr_count != 1 else ''} to other repos")
            evidence = " (" + ", ".join(parts[1:]) + ")" if len(parts) > 1 else ""
            evidence = parts[0] + evidence
        elif recent >= 1:
            level = SkillLevel.familiar
            evidence = f"GitHub: {total} own repo{'s' if total != 1 else ''} in {lang}, pushed in the last year"
        else:
            continue

        skills.append(Skill(name=lang, level=level, evidence=evidence))

    return skills


# ---------------------------------------------------------------------------
# Profile merging
# ---------------------------------------------------------------------------

def merge_github_into_profile(profile: Profile, sig: dict) -> None:
    """
    Merge GitHub-derived skills and topics into *profile* in-place.

    - If a skill already exists (case-insensitive):
        - GitHub level is higher  → upgrade level, replace evidence
        - GitHub level is equal/lower → append GitHub evidence to existing
    - If the skill doesn't exist → add it
    - Add up to 5 topics from sig["top_topics"] to profile.interests (no dupes)
    """
    _level_rank = {
        SkillLevel.experienced: 2,
        SkillLevel.familiar: 1,
        SkillLevel.inferred: 0,
    }

    gh_skills = skills_from_signals(sig)

    # Build a lookup of existing skills by lower-cased name
    existing: dict[str, Skill] = {s.name.lower(): s for s in profile.skills}

    for gh_skill in gh_skills:
        key = gh_skill.name.lower()
        if key in existing:
            current = existing[key]
            if _level_rank[gh_skill.level] > _level_rank[current.level]:
                current.level = gh_skill.level
                current.evidence = gh_skill.evidence
            else:
                current.evidence = f"{current.evidence}; {gh_skill.evidence}"
        else:
            profile.skills.append(gh_skill)
            existing[key] = gh_skill

    # Topics → interests (up to 5 new ones)
    existing_interests_lower = {i.lower() for i in profile.interests}
    added = 0
    for topic in sig.get("top_topics", []):
        if added >= 5:
            break
        if topic.lower() not in existing_interests_lower:
            profile.interests.append(topic)
            existing_interests_lower.add(topic.lower())
            added += 1
