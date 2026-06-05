"""Create draft pull requests via gh CLI.

Push requires write access to ``origin`` (typically a user fork). Upstream-only
clones of approved OSS repos cannot push without fork remote configuration.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from go_agent.branching import BranchInfo
from go_agent.git_util import GitCommandError, run_git
from go_agent.pr_writer import PRDraft, render_pr_body
from go_agent.run_context import RunContext

_GH_TIMEOUT = 60
_PR_URL_RE = re.compile(r"https://github\.com/[\w./-]+/pull/\d+")


class PRCreateError(RuntimeError):
    """Raised when branch push or gh pr create fails."""


@dataclass(frozen=True)
class PRResult:
    url: str
    title: str
    branch_name: str


def count_commits_ahead(repo_path: Path, base_sha: str) -> int:
    """Return number of commits on HEAD not reachable from base_sha."""
    try:
        output = run_git(["rev-list", "--count", f"{base_sha}..HEAD"], cwd=repo_path)
    except GitCommandError as exc:
        raise PRCreateError(f"could not count commits ahead of base: {exc}") from exc
    try:
        return int(output)
    except ValueError as exc:
        raise PRCreateError(f"unexpected rev-list output: {output!r}") from exc


def push_branch(repo_path: Path, branch_name: str, logger: logging.Logger) -> None:
    logger.info("Pushing branch %s to origin", branch_name)
    try:
        run_git(["push", "-u", "origin", branch_name], cwd=repo_path)
    except GitCommandError as exc:
        raise PRCreateError(f"git push failed: {exc}") from exc


def _parse_pr_url(stdout: str) -> str:
    for line in stdout.splitlines():
        match = _PR_URL_RE.search(line.strip())
        if match:
            return match.group(0)
    raise PRCreateError(f"gh pr create did not return a PR URL: {stdout.strip()!r}")


def create_draft_pr(
    repo_path: Path,
    repo: str,
    base_branch: str,
    branch_name: str,
    title: str,
    body: str,
    logger: logging.Logger,
) -> PRResult:
    """Run ``gh pr create --draft`` and return the new PR URL."""
    if not shutil.which("gh"):
        raise PRCreateError("Install and authenticate `gh` CLI to create pull requests.")

    logger.info("Creating draft PR for %s branch %s", repo, branch_name)
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--repo",
                repo,
                "--base",
                base_branch,
                "--head",
                branch_name,
                "--title",
                title,
                "--body",
                body,
            ],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise PRCreateError(
            f"gh pr create failed: {stderr or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PRCreateError(f"gh pr create timed out after {_GH_TIMEOUT}s") from exc

    url = _parse_pr_url(result.stdout)
    logger.info("Draft PR created: %s", url)
    return PRResult(url=url, title=title, branch_name=branch_name)


def write_pr_meta(ctx: RunContext, result: PRResult) -> Path:
    meta = {
        "url": result.url,
        "title": result.title,
        "branch_name": result.branch_name,
    }
    path = ctx.artifact_dir / "pr_meta.json"
    path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path


def maybe_create_pr(
    repo_path: Path,
    repo: str,
    branch: BranchInfo,
    draft: PRDraft,
    ctx: RunContext,
    logger: logging.Logger,
) -> PRResult:
    """Push branch and open a draft PR when commits exist beyond the base SHA."""
    ahead = count_commits_ahead(repo_path, branch.base_sha)
    if ahead <= 0:
        raise PRCreateError(
            "Branch has no commits beyond base; apply changes before --create-pr."
        )

    if not shutil.which("gh"):
        raise PRCreateError("Install and authenticate `gh` CLI to create pull requests.")

    push_branch(repo_path, branch.branch_name, logger)
    body = render_pr_body(draft)
    result = create_draft_pr(
        repo_path,
        repo,
        branch.default_branch,
        branch.branch_name,
        draft.title,
        body,
        logger,
    )
    write_pr_meta(ctx, result)
    return result
