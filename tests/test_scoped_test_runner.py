"""Tests for scoped go test -run derivation and two-phase test runner."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from go_agent.config import Settings
from go_agent.fixer import parse_failing_packages
from go_agent.planner import FixPlan
from go_agent.test_runner import (
    TestRunError,
    derive_compile_check_commands,
    derive_scoped_test_commands,
    run_tests,
    tokenize_test_command,
)

VALIDATOR_FAIL_OUTPUT = """--- FAIL: TestUnixAddrValidation (0.00s)
    validator_test.go:3048: Index: 0 unix_addr failed Error: Key: '' Error:Field validation for '' failed on the 'unix_addr' tag
FAIL
FAIL\tgithub.com/go-playground/validator/v10\t0.163s
ok  \tgithub.com/go-playground/validator/v10/translations/ar\t1.031s
FAIL
"""

MULTI_TEST_OUTPUT = """--- FAIL: TestAlpha (0.00s)
--- FAIL: TestBeta (0.00s)
FAIL
FAIL\tgithub.com/example/pkg\t0.010s
"""


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=1348,
        repo="go-playground/validator",
        files=["baked_in.go"],
        steps=["fix"],
        test_commands=["go test -race ./... -count=1"],
        acceptance_criteria=["pass"],
    )


COMPILE_FAIL_OUTPUT = (
    "FAIL\tgithub.com/go-playground/validator/v10 [setup failed]\n"
    "./baked_in.go:1715:1: syntax error: non-declaration statement outside function body\n"
)


def test_derive_compile_check_commands(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")

    derived = derive_compile_check_commands(COMPILE_FAIL_OUTPUT, repo_path)
    assert len(derived) == 1
    argv, cwd = derived[0]
    assert argv == ["go", "build", "./..."]
    assert cwd == repo_path


def test_parse_failing_packages():
    packages = parse_failing_packages(VALIDATOR_FAIL_OUTPUT)
    assert packages == ["github.com/go-playground/validator/v10"]


def test_derive_scoped_from_validator_sample(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    package_dir = repo_path / "validator"
    package_dir.mkdir()

    with patch("go_agent.test_runner._resolve_package_dir", return_value=package_dir):
        derived = derive_scoped_test_commands(
            VALIDATOR_FAIL_OUTPUT,
            repo_path,
            base_commands=["go test -race ./... -count=1"],
        )

    assert len(derived) == 1
    argv, cwd = derived[0]
    assert argv == [
        "go",
        "test",
        "-race",
        "-count=1",
        "-run",
        "^TestUnixAddrValidation$",
        ".",
    ]
    assert cwd == package_dir


def test_derive_groups_multiple_tests_per_package(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    package_dir = repo_path / "pkg"
    package_dir.mkdir()

    with patch("go_agent.test_runner._resolve_package_dir", return_value=package_dir):
        derived = derive_scoped_test_commands(
            MULTI_TEST_OUTPUT,
            repo_path,
            base_commands=["go test -count=1 ./..."],
        )

    assert len(derived) == 1
    argv, _cwd = derived[0]
    assert argv[argv.index("-run") + 1] == "^(TestAlpha|TestBeta)$"


def test_derive_returns_empty_without_failures(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    derived = derive_scoped_test_commands("ok\tgithub.com/example/pkg\t0.01s", repo_path)
    assert derived == []


def test_tokenize_rejects_shell_metacharacters():
    for unsafe in [
        "go test ./...; rm -rf /",
        "go test ./... | cat",
        "go test ./... && echo pwned",
        "go test `whoami`",
        "go test $(whoami)",
    ]:
        with pytest.raises(TestRunError, match="unsafe shell"):
            tokenize_test_command(unsafe)


def test_run_tests_scoped_on_iteration_gt_zero(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    settings = Settings(scoped_test_enabled=True, scoped_test_before_review_full=False)

    prior_output = VALIDATOR_FAIL_OUTPUT
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("Completed", (), {"returncode": 1, "stdout": prior_output, "stderr": ""})()

    with patch("go_agent.test_runner.subprocess.run", side_effect=fake_run):
        with patch(
            "go_agent.test_runner._resolve_package_dir",
            return_value=repo_path,
        ):
            result = run_tests(
                repo_path,
                _plan(),
                "go-playground/validator",
                settings,
                iteration=1,
                max_fix_iterations=5,
                prior_test_output=prior_output,
            )

    assert result.mode == "scoped"
    assert result.scoped_from_failure is True
    assert result.passed is False
    assert any("-run" in call for call in calls)
    run_index = next(i for i, call in enumerate(calls) if "-run" in call)
    assert calls[run_index][calls[run_index].index("-run") + 1] == "^TestUnixAddrValidation$"


def test_run_tests_scoped_pass_runs_full_gate(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    settings = Settings(scoped_test_enabled=True, scoped_test_before_review_full=True)
    prior_output = VALIDATOR_FAIL_OUTPUT
    call_count = {"n": 0}

    def fake_run(argv, **kwargs):
        call_count["n"] += 1
        if "-run" in argv:
            return type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
        return type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("go_agent.test_runner.subprocess.run", side_effect=fake_run):
        with patch(
            "go_agent.test_runner._resolve_package_dir",
            return_value=repo_path,
        ):
            result = run_tests(
                repo_path,
                _plan(),
                "go-playground/validator",
                settings,
                iteration=2,
                max_fix_iterations=5,
                prior_test_output=prior_output,
            )

    assert result.mode == "scoped_then_full"
    assert result.passed is True
    assert result.scoped_from_failure is True
    assert call_count["n"] >= 2
    assert len(result.commands) >= 2


def test_run_tests_prior_passed_uses_full(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    settings = Settings(scoped_test_enabled=True)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("Completed", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    with patch("go_agent.test_runner.subprocess.run", side_effect=fake_run):
        result = run_tests(
            repo_path,
            _plan(),
            "go-playground/validator",
            settings,
            iteration=2,
            max_fix_iterations=5,
            prior_test_output=None,
        )

    assert result.mode == "full"
    assert result.scoped_from_failure is False
    assert all("-run" not in call for call in calls)


def test_write_test_result_includes_mode_and_command_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    from go_agent.config import clear_settings_cache
    from go_agent.run_context import create_run_context
    from go_agent.test_runner import TestRunResult, write_test_result

    clear_settings_cache()
    ctx = create_run_context()
    result = TestRunResult(
        passed=False,
        resolved_commands=["(cd /pkg) go test -run ^TestFoo$ ."],
        command_argv=[["go", "test", "-run", "^TestFoo$", "."]],
        source="plan",
        plan_commands=["go test ./... -count=1"],
        mode="scoped",
        scoped_from_failure=True,
    )
    path = write_test_result(ctx, result)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["mode"] == "scoped"
    assert payload["scoped_from_failure"] is True
    assert payload["command_argv"] == [["go", "test", "-run", "^TestFoo$", "."]]
