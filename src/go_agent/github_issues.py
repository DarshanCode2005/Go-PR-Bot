"""Minimal GitHub issue access (title only until full IssueContext in a later issue)."""

from __future__ import annotations

import shutil
import subprocess

from go_agent.config import Settings


class IssueFetchError(RuntimeError):
    """Raised when the issue title cannot be fetched."""


def _fetch_via_gh(repo: str, issue_number: int) -> str | None:
    if not shutil.which("gh"):
        return None
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "title",
                "-q",
                ".title",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    title = result.stdout.strip()
    return title or None


def _fetch_via_pygithub(repo: str, issue_number: int, token: str) -> str | None:
    try:
        from github import Github
    except ImportError:
        return None

    try:
        client = Github(token)
        gh_repo = client.get_repo(repo)
        issue = gh_repo.get_issue(issue_number)
        title = issue.title
    except Exception:
        return None
    return title.strip() if title else None


def fetch_issue_title(repo: str, issue_number: int, settings: Settings) -> str:
    """Fetch issue title via gh CLI or PyGithub."""
    title = _fetch_via_gh(repo, issue_number)
    if title:
        return title

    if settings.github_token:
        title = _fetch_via_pygithub(repo, issue_number, settings.github_token)
        if title:
            return title

    raise IssueFetchError(
        "Could not fetch issue title. Install and authenticate `gh`, or set GITHUB_TOKEN."
    )
