"""Test helpers for git fixtures and planner mocks."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

MOCK_PLAN_JSON = (
    '{"files":["context.go"],"steps":["Add nil guard"],'
    '"test_commands":["go test ./... -count=1"],'
    '"acceptance_criteria":["Tests pass"]}'
)


def run_git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def enable_planner_mock(
    monkeypatch: Any,
    *,
    transport: Callable[..., str] | None = None,
) -> None:
    """Configure env + transport so CLI runs pass the planner step in tests."""
    from go_agent.config import clear_settings_cache

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    effective_transport = transport or (lambda **_: MOCK_PLAN_JSON)
    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", effective_transport)


def bump_bare_repo(bare_repo_url: str, tmp_path: Path, *, content: str = "v2\n") -> str:
    """Push a new commit to an existing bare repo; return its file:// URL."""
    bare = Path(bare_repo_url.removeprefix("file://"))
    work = tmp_path / "bump_work"
    if work.exists():
        shutil.rmtree(work)
    run_git(["clone", bare_repo_url, str(work)], cwd=tmp_path)
    run_git(["config", "user.email", "test@example.com"], cwd=work)
    run_git(["config", "user.name", "Test"], cwd=work)
    (work / "README.md").write_text(content, encoding="utf-8")
    run_git(["commit", "-am", "bump"], cwd=work)
    run_git(["push", "origin", "HEAD"], cwd=work)
    return bare.as_uri()
