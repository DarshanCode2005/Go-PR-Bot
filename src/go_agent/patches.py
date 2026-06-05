"""Apply unified diffs, commit changes, and export patch artifacts."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from go_agent.git_util import GIT_TIMEOUT, GitCommandError, run_git
from go_agent.run_context import RunContext

_WHITESPACE = re.compile(r"\s+")


class PatchApplyError(RuntimeError):
    """Raised when patch apply or commit fails."""


@dataclass(frozen=True)
class PatchResult:
    commit_sha: str
    commit_message: str
    changes_patch_path: Path


def format_commit_message(summary: str, issue_number: int, *, kind: str = "fix") -> str:
    cleaned = _WHITESPACE.sub(" ", summary.strip())
    if len(cleaned) > 72:
        cleaned = cleaned[:72].rstrip()
    if not cleaned:
        cleaned = "update code"
    return f"{kind}: {cleaned} (fixes #{issue_number})"


def apply_unified_patch(repo_path: Path, patch: str) -> None:
    """Validate and apply a unified diff in repo_path."""
    if not patch.strip():
        raise PatchApplyError("patch is empty")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".patch",
        delete=False,
        encoding="utf-8",
    ) as handle:
        handle.write(patch)
        if not patch.endswith("\n"):
            handle.write("\n")
        patch_path = Path(handle.name)

    try:
        _git_apply(repo_path, patch_path)
    finally:
        patch_path.unlink(missing_ok=True)


def _git_apply(repo_path: Path, patch_path: Path) -> None:
    for check_only in (True, False):
        args = ["apply"]
        if check_only:
            args.append("--check")
        args.append(str(patch_path))
        try:
            run_git(args, cwd=repo_path)
        except GitCommandError as exc:
            flag = "--check " if check_only else ""
            raise PatchApplyError(
                f"git apply {flag}failed: {exc}. "
                f"Inspect the patch and run `git apply --check` in {repo_path}."
            ) from exc


def commit_all(repo_path: Path, message: str) -> str:
    """Stage all changes and create a commit; return the new commit SHA."""
    try:
        run_git(["add", "-A"], cwd=repo_path)
        run_git(["commit", "-m", message], cwd=repo_path)
        return run_git(["rev-parse", "HEAD"], cwd=repo_path)
    except GitCommandError as exc:
        err = str(exc).lower()
        if "nothing to commit" in err or "no changes added to commit" in err:
            raise PatchApplyError(
                "nothing to commit after applying patch; patch may not modify tracked files"
            ) from exc
        raise PatchApplyError(f"git commit failed: {exc}") from exc


def export_changes_patch(repo_path: Path, base_sha: str, dest: Path) -> Path:
    """Write git diff from base_sha to the working tree to dest."""
    try:
        untracked = run_git(
            ["ls-files", "-o", "--exclude-standard"],
            cwd=repo_path,
        )
        for path in untracked.splitlines():
            if path.strip():
                run_git(["add", "-N", path], cwd=repo_path)
    except GitCommandError as exc:
        raise PatchApplyError(f"git add -N failed: {exc}") from exc

    try:
        result = subprocess.run(
            ["git", "diff", base_sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise PatchApplyError(
            f"git diff failed: {stderr or exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PatchApplyError(f"git diff timed out after {GIT_TIMEOUT}s") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(result.stdout, encoding="utf-8")
    return dest


def _reset_working_tree(repo_path: Path) -> None:
    run_git(["reset", "--hard", "HEAD"], cwd=repo_path)


def apply_patch_and_commit(
    repo_path: Path,
    ctx: RunContext,
    patch: str,
    issue_number: int,
    summary: str,
    base_sha: str,
    logger: logging.Logger,
) -> PatchResult:
    """Apply patch, commit with standard message, and export changes.patch."""
    changes_path = ctx.artifact_dir / "changes.patch"
    message = format_commit_message(summary, issue_number)
    head = run_git(["rev-parse", "HEAD"], cwd=repo_path)

    if head != base_sha:
        if not changes_path.exists():
            logger.warning(
                "Branch already has commits beyond %s; re-exporting changes.patch",
                base_sha[:8],
            )
            export_changes_patch(repo_path, base_sha, changes_path)
        commit_message = run_git(["log", "-1", "--format=%s"], cwd=repo_path)
        logger.info("Branch already at %s; using existing commit", head[:8])
        return PatchResult(
            commit_sha=head,
            commit_message=commit_message,
            changes_patch_path=changes_path,
        )

    try:
        apply_unified_patch(repo_path, patch)
        run_git(["add", "-A"], cwd=repo_path)
        export_changes_patch(repo_path, base_sha, changes_path)
        commit_sha = commit_all(repo_path, message)
    except PatchApplyError:
        _reset_working_tree(repo_path)
        raise

    if not changes_path.read_text(encoding="utf-8").strip():
        logger.warning("changes.patch is empty for base %s", base_sha[:8])

    logger.info("Committed %s; wrote %s", commit_sha[:8], changes_path)
    return PatchResult(
        commit_sha=commit_sha,
        commit_message=message,
        changes_patch_path=changes_path,
    )
