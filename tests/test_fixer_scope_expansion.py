"""Tests for fixer failure parsing, test file resolution, and scope expansion."""

from __future__ import annotations

import json
import shutil

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.failure_parse import is_compile_failure
from go_agent.fixer import (
    FixContext,
    FixScopeExpansion,
    PlanExpansion,
    _failure_summary,
    expand_fix_files,
    parse_failing_tests,
    parse_referenced_go_files,
    resolve_test_files,
    write_plan_expansion,
)
from go_agent.planner import FixPlan
from go_agent.run_context import create_run_context


SAMPLE_TEST_OUTPUT = (
    "--- FAIL: TestUnixAddrValidation (0.00s)\n"
    "    validator_test.go:3048: Index: 0 unix_addr failed Error: Key: '' "
    "Error:Field validation for '' failed on the 'unix_addr' tag\n"
    "FAIL\n"
    "FAIL\tgithub.com/go-playground/validator/v10\t0.163s\n"
)

COMPILE_FAIL_OUTPUT = (
    "FAIL\tgithub.com/go-playground/validator/v10 [setup failed]\n"
    "# github.com/go-playground/validator/v10\n"
    "./baked_in.go:1715:1: syntax error: non-declaration statement outside function body\n"
    "validator_test.go:3225:1: expected declaration, found '}'\n"
)


def test_parse_failing_tests_standard_format():
    names = parse_failing_tests(SAMPLE_TEST_OUTPUT)
    assert names == ["TestUnixAddrValidation"]


def test_parse_failing_tests_multiple():
    output = "--- FAIL: TestFoo (0.00s)\n--- FAIL: TestBar (0.00s)\nFAIL: TestFoo\n"
    names = parse_failing_tests(output)
    assert names == ["TestFoo", "TestBar"]


def test_parse_referenced_go_files_file_line():
    paths = parse_referenced_go_files("baked_in.go:42: undefined x\n./pkg/foo.go:1: note")
    assert "baked_in.go" in paths
    assert "pkg/foo.go" in paths


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep required")
def test_resolve_test_files_finds_definition(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "foo_test.go").write_text(
        "package p\n\nfunc TestFoo(t *testing.T) {}\n",
        encoding="utf-8",
    )
    settings = Settings()
    resolved = resolve_test_files(repo_path, ["TestFoo"], settings)
    assert resolved == ["foo_test.go"]


def test_is_compile_failure_detects_syntax_errors():
    assert is_compile_failure(COMPILE_FAIL_OUTPUT)
    assert not is_compile_failure(SAMPLE_TEST_OUTPUT)


def test_expand_compile_failure_limits_scope_to_production_files(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")
    (repo_path / "validator_test.go").write_text("package validator\n", encoding="utf-8")

    plan = FixPlan(
        issue_number=1348,
        repo="go-playground/validator",
        files=["baked_in.go", "validator_test.go"],
        steps=["fix"],
        test_commands=["go test -count=1 ./..."],
        acceptance_criteria=["pass"],
    )
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output=COMPILE_FAIL_OUTPUT,
    )
    expansion = expand_fix_files(plan, ctx, repo_path, Settings())
    assert expansion.target_files == ["baked_in.go"]
    assert "validator_test.go" not in expansion.target_files


def test_expand_includes_ripgrep_resolved_test_file(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")
    (repo_path / "validator_test.go").write_text(
        "package validator\n\nfunc TestUnixAddrValidation(t *testing.T) {}\n",
        encoding="utf-8",
    )

    plan = FixPlan(
        issue_number=1348,
        repo="go-playground/validator",
        files=["baked_in.go"],
        steps=["fix"],
        test_commands=["go test -count=1"],
        acceptance_criteria=["pass"],
    )
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output="--- FAIL: TestUnixAddrValidation (0.00s)\n",
    )
    expansion = expand_fix_files(plan, ctx, repo_path, Settings())
    assert "baked_in.go" in expansion.target_files
    assert "validator_test.go" in expansion.target_files
    assert "validator_test.go" in expansion.added_files
    assert expansion.failing_tests == ["TestUnixAddrValidation"]


def test_write_plan_expansion_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    ctx = create_run_context()
    path = write_plan_expansion(
        ctx,
        PlanExpansion(
            iteration=2,
            original_files=["baked_in.go"],
            added_files=["validator_test.go"],
            failing_tests=["TestUnixAddrValidation"],
            reason="Failing tests: TestUnixAddrValidation",
        ),
    )
    assert path == ctx.artifact_dir / "plan_expansion.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["added_files"] == ["validator_test.go"]
    assert data["failing_tests"] == ["TestUnixAddrValidation"]


def test_failure_summary_lists_allowed_files():
    ctx = FixContext(
        iteration=1,
        max_iterations=5,
        failure_source="test",
        test_output="--- FAIL: TestFoo",
    )
    scope = FixScopeExpansion(
        target_files=["a.go", "a_test.go"],
        added_files=["a_test.go"],
        failing_tests=["TestFoo"],
        reason="test failure",
    )
    summary = _failure_summary(ctx, scope=scope)
    assert "Allowed files for this fix iteration: a.go, a_test.go" in summary
    assert "Failing tests: TestFoo" in summary


def test_fixer_prompt_lists_allowed_files(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "main.go").write_text("package main\n", encoding="utf-8")

    captured: list[list[dict]] = []

    def capture_transport(*, model=None, messages=None, temperature=None):
        _ = model, temperature
        captured.append(list(messages or []))
        return "--- SEARCH\npackage main\n+++ REPLACE\npackage main\n// fixed\n"

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    from go_agent.llm_client import set_completion_transport

    set_completion_transport(capture_transport)

    from go_agent.context_builder import ContextBundle
    from go_agent.fixer import build_corrective_patch
    from go_agent.github_issues import IssueContext

    plan = FixPlan(
        issue_number=1,
        repo="example/repo",
        files=["main.go"],
        steps=["fix"],
        test_commands=["go test ./..."],
        acceptance_criteria=["pass"],
    )
    result = build_corrective_patch(
        repo_path,
        IssueContext(repo="example/repo", number=1, title="t", state="open"),
        plan,
        ContextBundle(
            repo="example/repo",
            issue_number=1,
            files=[],
            total_chars=0,
            budget_chars=12000,
        ),
        FixContext(
            iteration=1,
            max_iterations=5,
            failure_source="test",
            test_output="FAIL",
        ),
        Settings(),
    )
    assert result.artifact.files
    assert captured
    user_content = " ".join(m["content"] for m in captured[0] if m["role"] == "user")
    assert "Allowed files for this fix iteration" in user_content
