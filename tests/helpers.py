"""Test helpers for git fixtures and planner/coder mocks."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

MOCK_PLAN_JSON = (
    '{"files":["README.md"],"steps":["Update readme"],'
    '"test_commands":["go test ./... -count=1"],'
    '"acceptance_criteria":["Tests pass"]}'
)

def mock_coder_search_replace(messages: list[dict[str, str]]) -> str:
    """Build a one-line SEARCH/REPLACE patch from coder user prompt file content."""
    user = messages[-1]["content"] if messages else ""
    marker = "Current file content:\n"
    if marker not in user:
        return "--- SEARCH\nv1\n+++ REPLACE\nv2\n"
    content = user.split(marker, 1)[1].split("\n\n", 1)[0]
    lines = content.splitlines()
    if not lines:
        return "--- SEARCH\nx\n+++ REPLACE\ny\n"
    first = lines[0]
    return f"--- SEARCH\n{first}\n+++ REPLACE\n{first}.\n"

MOCK_SCOPE_JSON = '{"scope_hints": []}'
MOCK_SUMMARY = "Summary of the file for the coding agent."
MOCK_REVIEW_JSON = (
    '{"decision":"approve","comments":["Tests and lint passed; change matches issue scope"],'
    '"checklist":{"acceptance_criteria":true,"tests":true,"api_breaks":true,'
    '"style":true,"error_messages":true}}'
)


def mock_build_review(*args, **kwargs):
    from go_agent.reviewer import ReviewChecklist, ReviewResult

    return ReviewResult(
        decision="approve",
        comments=["Mock review: tests and lint passed"],
        checklist=ReviewChecklist(
            acceptance_criteria=True,
            tests=True,
            api_breaks=True,
            style=True,
            error_messages=True,
        ),
    )


def mock_run_tests(*args, **kwargs):
    from go_agent.test_runner import CommandResult, TestRunResult

    command = "go test ./... -count=1"
    return TestRunResult(
        passed=True,
        commands=[
            CommandResult(
                command=command,
                exit_code=0,
                passed=True,
                stdout="ok",
                stderr="",
                duration_seconds=0.1,
            )
        ],
        resolved_commands=[command],
        source="plan",
        plan_commands=[command],
    )


def mock_run_lints(*args, **kwargs):
    from go_agent.lint_runner import LintRunResult
    from go_agent.test_runner import CommandResult

    command = "go vet ./..."
    return LintRunResult(
        passed=True,
        commands=[
            CommandResult(
                command=command,
                exit_code=0,
                passed=True,
                stdout="ok",
                stderr="",
                duration_seconds=0.1,
            )
        ],
        resolved_commands=[command],
        source="default",
        findings=[],
    )


def mock_build_corrective_patch(*args, **kwargs):
    from go_agent.coder import CoderArtifact, FilePatch
    from go_agent.fixer import CorrectivePatchResult, FixScopeExpansion

    patch = FilePatch(
        path="README.md",
        format="search_replace",
        patch="--- a/README.md\n+++ b/README.md\n",
    )
    artifact = CoderArtifact(
        issue_number=1,
        repo="gin-gonic/gin",
        files=[patch],
        combined_patch=patch.patch,
        execution_waves=[["README.md"]],
    )
    expansion = FixScopeExpansion(
        target_files=["README.md"],
        added_files=[],
        failing_tests=[],
        reason="No scope expansion",
    )
    return CorrectivePatchResult(artifact=artifact, expansion=expansion)


def run_git(args: list[str], *, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def init_git_repo(repo_path: Path, *, files: dict[str, str] | None = None) -> None:
    """Initialize a git repo with an initial commit."""
    repo_path.mkdir(parents=True, exist_ok=True)
    run_git(["init"], cwd=repo_path)
    run_git(["config", "user.email", "test@example.com"], cwd=repo_path)
    run_git(["config", "user.name", "Test"], cwd=repo_path)
    for rel_path, content in (files or {"README.md": "hello\n"}).items():
        target = repo_path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run_git(["add", rel_path], cwd=repo_path)
    run_git(["commit", "-m", "init"], cwd=repo_path)


def agent_mock_transport(
    *,
    model: str | None = None,
    messages: list[dict[str, str]] | None = None,
    temperature: float | None = None,
) -> str:
    """Dispatch mock LLM responses by prompt content for CLI integration tests."""
    _ = model, temperature
    messages = messages or []
    system = messages[0]["content"] if messages else ""
    user = messages[-1]["content"] if messages else ""

    if "Return JSON only" in system and "files" in system:
        return MOCK_PLAN_JSON
    if "scope_hints" in user and "Return JSON only" in user:
        return MOCK_SCOPE_JSON
    if "Summarize this Go source file" in user:
        return MOCK_SUMMARY
    if "maintainer reviewing" in system.lower() or (
        "decision" in system and "checklist" in system
    ):
        return MOCK_REVIEW_JSON
    if "coder agent" in system.lower() or "SEARCH/REPLACE" in system:
        return mock_coder_search_replace(messages)
    return mock_coder_search_replace(messages)


def enable_agent_mocks(
    monkeypatch: Any,
    *,
    transport: Callable[..., str] | None = None,
) -> None:
    """Configure env + transport so CLI runs pass planner and coder steps in tests."""
    from go_agent.config import clear_settings_cache

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    effective_transport = transport or agent_mock_transport
    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", effective_transport)
    monkeypatch.setattr("go_agent.orchestrator.nodes.run_tests", mock_run_tests)
    monkeypatch.setattr("go_agent.orchestrator.nodes.run_lints", mock_run_lints)
    monkeypatch.setattr(
        "go_agent.orchestrator.nodes.build_corrective_patch",
        mock_build_corrective_patch,
    )
    monkeypatch.setattr("go_agent.orchestrator.nodes.build_review", mock_build_review)


def enable_planner_mock(
    monkeypatch: Any,
    *,
    transport: Callable[..., str] | None = None,
) -> None:
    """Backward-compatible alias for enable_agent_mocks."""
    enable_agent_mocks(monkeypatch, transport=transport)


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


def list_run_artifact_dirs(artifacts_dir: Path) -> list[Path]:
    """Return per-run artifact directories, excluding shared checkpoints storage."""
    return [
        p for p in artifacts_dir.iterdir() if p.is_dir() and p.name != "checkpoints"
    ]
