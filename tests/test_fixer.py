"""Tests for fix agent and failure context building."""

from __future__ import annotations

import pytest

from go_agent.fixer import (
    FixContext,
    FixError,
    FixMeta,
    build_corrective_patch,
    build_failure_context,
    build_review_fix_context,
    expand_fix_files,
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


def test_build_review_fix_context_from_review_state():
    ctx = build_review_fix_context(
        {
            "review": {
                "decision": "request_changes",
                "comments": ["Fix error message in foo.go:12"],
            },
            "iteration": 0,
            "review_round": 0,
        },
        max_review_rounds=1,
        max_iterations=5,
    )
    assert ctx.failure_source == "review"
    assert ctx.review_round == 1
    assert "foo.go" in ctx.review_comments[0]


def test_failure_summary_includes_review_feedback():
    from go_agent.fixer import _failure_summary

    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="review",
        review_comments=["Fix foo.go:12 formatting"],
        review_round=1,
    )
    summary = _failure_summary(ctx)
    assert "Review feedback" in summary
    assert "foo.go:12" in summary


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
    assert artifact.artifact.files
    assert artifact.artifact.combined_patch


def test_build_corrective_patch_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROQ_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    from go_agent.config import clear_settings_cache

    clear_settings_cache()
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


def test_expand_fix_files_adds_mentioned_test_file(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")
    (repo_path / "validator_test.go").write_text("package validator\n", encoding="utf-8")

    plan = FixPlan(
        issue_number=1,
        repo="go-playground/validator",
        files=["baked_in.go"],
        steps=["Fix unix_addr"],
        test_commands=["go test -run TestUnixAddrValidation -count=1"],
        acceptance_criteria=["Tests pass"],
    )
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output="--- FAIL: TestUnixAddrValidation (0.00s)\n    validator_test.go:123: assertion failed",
    )
    settings = __import__("go_agent.config", fromlist=["Settings"]).Settings()
    expanded = expand_fix_files(plan, ctx, repo_path, settings)
    assert "baked_in.go" in expanded.target_files
    assert "validator_test.go" in expanded.target_files


def test_expand_fix_files_skips_unknown_paths(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "main.go").write_text("package main\n", encoding="utf-8")

    plan = _plan()
    plan = plan.model_copy(update={"files": ["main.go"]})
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output="FAIL: missing.go:10: undefined",
    )
    settings = __import__("go_agent.config", fromlist=["Settings"]).Settings()
    expanded = expand_fix_files(plan, ctx, repo_path, settings)
    assert expanded.target_files == ["main.go"]


def test_expand_fix_files_caps_extra_files(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    for name in ("a.go", "b_test.go", "c_test.go", "d_test.go"):
        (repo_path / name).write_text("package p\n", encoding="utf-8")

    plan = FixPlan(
        issue_number=1,
        repo="example/repo",
        files=["a.go"],
        steps=["fix"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["pass"],
    )
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output="FAIL b_test.go c_test.go d_test.go",
    )
    settings = __import__("go_agent.config", fromlist=["Settings"]).Settings()
    expanded = expand_fix_files(plan, ctx, repo_path, settings, max_extra=2)
    assert len(expanded.target_files) == 3
    assert expanded.target_files[0] == "a.go"


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
