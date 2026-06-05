"""GitHub issue fetch and IssueContext models."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.run_context import RunContext

_GH_JSON_FIELDS = "title,body,labels,state,comments"
_GH_TIMEOUT = 60


class IssueFetchError(RuntimeError):
    """Raised when issue metadata cannot be fetched."""


class ClosedIssueError(RuntimeError):
    """Raised when a closed issue is used without --force."""


class IssueComment(BaseModel):
    author: str
    body: str
    created_at: str | None = None


class IssueContext(BaseModel):
    repo: str
    number: int
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    state: str
    comments: list[IssueComment] = Field(default_factory=list)

    @property
    def is_closed(self) -> bool:
        return self.state.lower() == "closed"


def _cap_comments(comments: list[IssueComment], max_comments: int) -> list[IssueComment]:
    if max_comments <= 0:
        return []
    if len(comments) <= max_comments:
        return comments
    return comments[-max_comments:]


def _parse_gh_payload(repo: str, issue_number: int, payload: dict[str, Any], max_comments: int) -> IssueContext:
    labels = [label.get("name", "") for label in payload.get("labels", []) if label.get("name")]
    raw_comments = payload.get("comments") or []
    comments: list[IssueComment] = []
    for item in raw_comments:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        login = author.get("login") if isinstance(author, dict) else None
        comments.append(
            IssueComment(
                author=str(login or "unknown"),
                body=str(item.get("body") or ""),
                created_at=item.get("createdAt"),
            )
        )
    return IssueContext(
        repo=repo,
        number=issue_number,
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        labels=labels,
        state=str(payload.get("state") or "open").lower(),
        comments=_cap_comments(comments, max_comments),
    )


def _fetch_context_via_gh(repo: str, issue_number: int, max_comments: int) -> IssueContext | None:
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
                _GH_JSON_FIELDS,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=_GH_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not payload.get("title"):
        return None
    return _parse_gh_payload(repo, issue_number, payload, max_comments)


def _fetch_context_via_pygithub(
    repo: str,
    issue_number: int,
    token: str,
    max_comments: int,
) -> IssueContext | None:
    try:
        from github import Github
    except ImportError:
        return None

    try:
        client = Github(token)
        gh_repo = client.get_repo(repo)
        issue = gh_repo.get_issue(issue_number)
        comments: list[IssueComment] = []
        for comment in issue.get_comments():
            comments.append(
                IssueComment(
                    author=getattr(comment.user, "login", "unknown"),
                    body=comment.body or "",
                    created_at=comment.created_at.isoformat() if comment.created_at else None,
                )
            )
        comments = _cap_comments(comments, max_comments)
        labels = [label.name for label in issue.labels]
        return IssueContext(
            repo=repo,
            number=issue_number,
            title=issue.title or "",
            body=issue.body or "",
            labels=labels,
            state=(issue.state or "open").lower(),
            comments=comments,
        )
    except Exception:
        return None


def fetch_issue_context(
    repo: str,
    issue_number: int,
    settings: Settings,
    *,
    max_comments: int | None = None,
) -> IssueContext:
    """Fetch full issue metadata via gh CLI or PyGithub."""
    cap = max_comments if max_comments is not None else settings.max_issue_comments

    context = _fetch_context_via_gh(repo, issue_number, cap)
    if context is not None:
        return context

    if settings.github_token:
        context = _fetch_context_via_pygithub(
            repo,
            issue_number,
            settings.github_token,
            cap,
        )
        if context is not None:
            return context

    raise IssueFetchError(
        "Could not fetch issue metadata. Install and authenticate `gh`, or set GITHUB_TOKEN."
    )


def fetch_issue_title(repo: str, issue_number: int, settings: Settings) -> str:
    """Fetch issue title via full issue context."""
    return fetch_issue_context(repo, issue_number, settings).title


def ensure_issue_open_or_forced(
    issue: IssueContext,
    *,
    force: bool,
    logger: logging.Logger,
) -> None:
    if not issue.is_closed:
        return
    if force:
        logger.warning(
            "Issue #%s is closed; proceeding due to --force",
            issue.number,
        )
        return
    raise ClosedIssueError(
        f"Issue #{issue.number} is closed. Re-open it or pass --force."
    )


def write_issue_context(ctx: RunContext, issue: IssueContext) -> Path:
    path = ctx.artifact_dir / "issue_context.json"
    path.write_text(
        issue.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return path
