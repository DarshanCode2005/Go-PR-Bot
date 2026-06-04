"""Shared git subprocess helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT = 300


class GitCommandError(RuntimeError):
    """Raised when a git command fails."""


def run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitCommandError(stderr or f"git {' '.join(args)} failed") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCommandError(f"git timed out after {GIT_TIMEOUT}s") from exc
    return result.stdout.strip()
