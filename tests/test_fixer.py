"""Tests for fix agent and failure context building."""

from __future__ import annotations

import pytest

from go_agent.fixer import (
    FixContext,
    FixError,
    FixMeta,
    build_corrective_patch,
    build_failure_context,
    write_fix_meta,
)
from go_agent.planner import FixPlan
from go_agent.run_context import create_run_context
from helpers import agent_mock_transport


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=1,
        repo="gin-gonic/gin",
        files=["README.md"],
        steps=["Update readme"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Tests pass"],
    )


def _bundle():
    from go_agent.context_builder import ContextBundle

    return ContextBundle(
        repo="gin-gonic/gin",
        issue_number=1,
        files=[],
        total_chars=0,
        budget_chars=12000,
    )


def _issue():
    from go_agent.github_issues import IssueContext

    return IssueContext(
        repo="gin-gonic/gin",
        number=1,
        title="Update readme",
        state="open",
    )


def test_build_failure_context_from_test_state():
    ctx = build_failure_context(
        {
            "last_node": "test",
            "iteration": 2,
            "test_result": {"passed": False, "output": "FAIL: main.go:10"},
        },
        max_iterations=5,
    )
    assert ctx.iteration == 3
    assert ctx.failure_source == "test"
    assert "main.go" in ctx.test_output


def test_build_failure_context_from_lint_state():
    ctx = build_failure_context(
        {
            "last_node": "lint",
            "iteration": 0,
            "lint_result": {
                "passed": False,
                "output": "vet failed",
                "findings": [{"file": "main.go", "line": 10, "message": "undefined: foo"}],
            },
        },
        max_iterations=5,
    )
    assert ctx.iteration == 1
    assert ctx.failure_source == "lint"
    assert ctx.lint_findings[0]["file"] == "main.go"


def test_build_corrective_patch_with_mock_llm(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from go_agent.config import clear_settings_cache

    clear_settings_cache()
    from go_agent.llm_client import set_completion_transport

    set_completion_transport(agent_mock_transport)

    artifact = build_corrective_patch(
        repo_path,
        _issue(),
        _plan(),
        _bundle(),
        FixContext(
            iteration=1,
            max_iterations=5,
            failure_source="test",
            test_output="FAIL",
        ),
        __import__("go_agent.config", fromlist=["Settings"]).Settings(),
    )
    assert artifact.files
    assert artifact.combined_patch


def test_build_corrective_patch_requires_api_key(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("hello\n", encoding="utf-8")

    with pytest.raises(FixError, match="API key"):
        build_corrective_patch(
            repo_path,
            _issue(),
            _plan(),
            _bundle(),
            FixContext(iteration=1, max_iterations=5, failure_source="test"),
            __import__("go_agent.config", fromlist=["Settings"]).Settings(),
        )


def test_write_fix_meta_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    from go_agent.config import clear_settings_cache

    clear_settings_cache()
    ctx = create_run_context()
    path = write_fix_meta(
        ctx,
        FixMeta(
            iteration=2,
            max_iterations=5,
            failure_source="lint",
            error_summary="vet failed",
            files=["main.go"],
        ),
    )
    assert path == ctx.artifact_dir / "fix_meta.json"
    assert path.exists()
