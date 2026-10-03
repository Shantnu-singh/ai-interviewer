"""Fetch a public GitHub profile and reduce it to a small, prompt-safe summary."""

from __future__ import annotations

from typing import Any

import httpx

API_BASE = "https://api.github.com"
MAX_REPOS = 30


class GitHubUserNotFound(Exception):
    pass


def _clip(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


def summarize(user: dict[str, Any], repos: list[dict[str, Any]]) -> dict[str, Any]:
    own = [r for r in repos if isinstance(r, dict) and not r.get("fork")]
    own.sort(key=lambda r: (r.get("stargazers_count") or 0, r.get("pushed_at") or ""), reverse=True)
    return {
        "login": _clip(user.get("login"), 39),
        "name": _clip(user.get("name"), 100),
        "bio": _clip(user.get("bio"), 300),
        "public_repos": user.get("public_repos"),
        "followers": user.get("followers"),
        "repos": [
            {
                "name": _clip(r.get("name"), 100),
                "description": _clip(r.get("description"), 200),
                "language": _clip(r.get("language"), 40),
                "stars": r.get("stargazers_count") or 0,
                "topics": [t[:40] for t in (r.get("topics") or [])[:5] if isinstance(t, str)],
            }
            for r in own[:MAX_REPOS]
        ],
    }


def fetch_profile(username: str, token: str = "", *, base_url: str = API_BASE) -> dict[str, Any]:
    """`username` must already be validated with GITHUB_USERNAME_RE."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-interviewer",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(
        base_url=base_url, headers=headers, timeout=10.0, follow_redirects=False
    ) as client:
        user = client.get(f"/users/{username}")
        if user.status_code == 404:
            raise GitHubUserNotFound(username)
        user.raise_for_status()
        repos = client.get(
            f"/users/{username}/repos",
            params={"per_page": 100, "sort": "pushed", "type": "owner"},
        )
        repos.raise_for_status()
    return summarize(user.json(), repos.json())
