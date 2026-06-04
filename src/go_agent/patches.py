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
            phase = "check" if check_only else "apply"
            raise PatchApplyError(
                f"git apply --{phase} failed: {exc}. "
                f"Inspect the patch and run `git apply --check` in {repo_path}."
            ) from exc


def commit_all(repo_path: Path, message: str) -> str:
    """Stage all changes and create a commit; return the new commit SHA."""
    try:
        run_git(["add", "-A"], cwd=repo_path)
        run_git(["commit", "-m", message], cwd=repo_path)
    except GitCommandError as exc:
        err = str(exc).lower()
        if "nothing to commit" in err or "no changes added to commit" in err:
            raise PatchApplyError(
                "nothing to commit after applying patch; patch may not modify tracked files"
            ) from exc
        raise PatchApplyError(f"git commit failed: {exc}") from exc
    return run_git(["rev-parse", "HEAD"], cwd=repo_path)


def export_changes_patch(repo_path: Path, base_sha: str, dest: Path) -> Path:
    """Write git diff from base_sha to HEAD to dest."""
    try:
        result = subprocess.run(
            ["git", "diff", f"{base_sha}..HEAD"],
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
    apply_unified_patch(repo_path, patch)
    message = format_commit_message(summary, issue_number)
    commit_sha = commit_all(repo_path, message)
    changes_path = ctx.artifact_dir / "changes.patch"
    export_changes_patch(repo_path, base_sha, changes_path)

    if not changes_path.read_text(encoding="utf-8").strip():
        logger.warning("changes.patch is empty for base %s", base_sha[:8])

    logger.info("Committed %s; wrote %s", commit_sha[:8], changes_path)
    return PatchResult(
        commit_sha=commit_sha,
        commit_message=message,
        changes_patch_path=changes_path,
    )
