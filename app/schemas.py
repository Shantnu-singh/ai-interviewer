"""Request and LLM-output schemas."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# GitHub rules: 1-39 chars, alphanumeric or single hyphens, not starting/ending with a hyphen.
GITHUB_USERNAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_GITHUB_HOSTS = {"github.com", "www.github.com"}


def parse_github_username(value: str) -> str:
    """Accept 'octocat', '@octocat', 'github.com/octocat' or 'https://github.com/octocat/'."""
    raw = value.strip()
    if not raw:
        raise ValueError("GitHub profile is required")
    if raw.startswith("@"):
        raw = raw[1:]

    if "/" in raw or "." in raw:
        candidate = raw if "://" in raw else f"https://{raw}"
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "") not in _GITHUB_HOSTS:
            raise ValueError("Enter a github.com profile URL or a username")
        segments = [s for s in parsed.path.split("/") if s]
        if len(segments) != 1:
            raise ValueError("Enter a profile URL like https://github.com/<username>")
        raw = segments[0]

    if not GITHUB_USERNAME_RE.fullmatch(raw):
        raise ValueError("That doesn't look like a valid GitHub username")
    return raw


class CreateInterviewBody(BaseModel):
    github: str = Field(max_length=200)

    @field_validator("github")
    @classmethod
    def _username(cls, v: str) -> str:
        return parse_github_username(v)


class Evaluation(BaseModel):
    score: int = Field(ge=0, le=10)
    feedback: str = Field(min_length=1, max_length=6000)
    strengths: list[str] = Field(default_factory=list, max_length=10)
    improvements: list[str] = Field(default_factory=list, max_length=10)
