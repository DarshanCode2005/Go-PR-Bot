"""Create per-issue git branches in the cloned workspace."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from go_agent.git_util import GitCommandError, run_git
from go_agent.run_context import RunContext
from go_agent.slug import issue_branch_name

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BranchError(RuntimeError):
    """Raised when branch creation fails."""


@dataclass(frozen=True)
class BranchInfo:
    branch_name: str
    base_sha: str
    default_branch: str
    issue_number: int
    issue_title: str


def _git(args: list[str], repo_path: Path) -> str:
    try:
        return run_git(args, cwd=repo_path)
    except GitCommandError as exc:
        raise BranchError(str(exc)) from exc


def resolve_default_branch(repo_path: Path) -> str:
    try:
        ref = _git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo_path)
        if ref.startswith("origin/"):
            return ref.removeprefix("origin/")
        return ref
    except BranchError:
        pass

    for candidate in ("main", "master"):
        try:
            _git(["rev-parse", f"origin/{candidate}"], repo_path)
            return candidate
        except BranchError:
            continue

    remote_branches = _git(["branch", "-r"], repo_path).splitlines()
    for line in remote_branches:
        line = line.strip()
        if "origin/HEAD" in line or "->" in line:
            continue
        if line.startswith("origin/"):
            return line.removeprefix("origin/").strip()
    raise BranchError("could not determine default branch")


def create_issue_branch(
    repo_path: Path,
    issue_number: int,
    issue_title: str,
    logger: logging.Logger,
) -> BranchInfo:
    """Checkout default branch and create agent/issue-{n}-{slug}."""
    default_branch = resolve_default_branch(repo_path)
    _git(["checkout", default_branch], repo_path)
    base_sha = _git(["rev-parse", "HEAD"], repo_path)
    if not _SHA_RE.match(base_sha):
        raise BranchError(f"invalid base SHA: {base_sha!r}")

    branch_name = issue_branch_name(issue_number, issue_title)
    existing = _git(["branch", "--list", branch_name], repo_path).strip()
    if existing:
        logger.warning("Branch %s already exists, checking out", branch_name)
        _git(["checkout", branch_name], repo_path)
        base_sha = _git(["merge-base", branch_name, default_branch], repo_path)
        if not _SHA_RE.match(base_sha):
            raise BranchError(f"invalid merge-base SHA: {base_sha!r}")
    else:
        _git(["checkout", "-b", branch_name], repo_path)
        logger.info("Created branch %s from %s", branch_name, base_sha[:8])

    return BranchInfo(
        branch_name=branch_name,
        base_sha=base_sha,
        default_branch=default_branch,
        issue_number=issue_number,
        issue_title=issue_title,
    )


def write_branch_meta(ctx: RunContext, branch: BranchInfo) -> Path:
    meta = {
        "branch_name": branch.branch_name,
        "base_sha": branch.base_sha,
        "default_branch": branch.default_branch,
        "issue_number": branch.issue_number,
        "issue_title": branch.issue_title,
    }
    path = ctx.artifact_dir / "branch_meta.json"
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path
