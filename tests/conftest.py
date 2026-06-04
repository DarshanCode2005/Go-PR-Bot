"""Shared pytest fixtures."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run_git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def bare_repo_url(tmp_path: Path) -> str:
    """Create a local bare repo with two commits; return file:// URL."""
    bare = tmp_path / "remote.git"
    work = tmp_path / "work"
    work.mkdir()
    _run_git(["init"], cwd=work)
    _run_git(["config", "user.email", "test@example.com"], cwd=work)
    _run_git(["config", "user.name", "Test"], cwd=work)
    (work / "README.md").write_text("v1\n", encoding="utf-8")
    _run_git(["add", "README.md"], cwd=work)
    _run_git(["commit", "-m", "v1"], cwd=work)
    _run_git(["init", "--bare", str(bare)], cwd=tmp_path)
    _run_git(["remote", "add", "origin", str(bare)], cwd=work)
    _run_git(["push", "-u", "origin", "HEAD"], cwd=work)
    return bare.as_uri()

