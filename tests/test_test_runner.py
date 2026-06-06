"""Tests for subprocess test runner and skill command resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from go_agent.planner import FixPlan
from go_agent.run_context import create_run_context
from go_agent.skills import parse_skill_test_commands, resolve_test_commands
from go_agent.test_runner import (
    TestRunError,
    TestRunResult,
    run_test_commands,
    write_test_result,
)

GIN_SKILL_WITH_FRONTMATTER = """---
test_commands:
  - go test ./... -count=1
---

# gin
"""

SKILL_WITH_BASH = """# repo

```bash
go test ./internal/... -count=1
```
"""


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=1,
        repo="gin-gonic/gin",
        files=["README.md"],
        steps=["step"],
        test_commands=["go test ./pkg/... -count=1"],
        acceptance_criteria=["pass"],
    )


def test_resolve_test_commands_uses_plan_by_default():
    commands, source = resolve_test_commands(_plan(), "spf13/cobra")
    assert source == "plan"
    assert commands == ["go test ./pkg/... -count=1"]


def test_resolve_test_commands_skill_frontmatter_override():
    commands, source = resolve_test_commands(_plan(), "gin-gonic/gin")
    assert source == "skill_override"
    assert commands == ["go test ./... -count=1"]


def test_parse_skill_test_commands_bash_block_fallback():
    commands = parse_skill_test_commands(SKILL_WITH_BASH)
    assert commands == ["go test ./internal/... -count=1"]


def test_parse_skill_test_commands_inline_bracket_list():
    skill = """---
test_commands: [go test ./... -count=1, go test ./pkg/... -count=1]
---
"""
    commands = parse_skill_test_commands(skill)
    assert commands == ["go test ./... -count=1", "go test ./pkg/... -count=1"]


def test_parse_skill_test_commands_inline_bracket_list_with_internal_comma():
    skill = """---
test_commands: ["go test -run TestFoo,TestBar ./... -count=1"]
---
"""
    commands = parse_skill_test_commands(skill)
    assert commands == ["go test -run TestFoo,TestBar ./... -count=1"]


def test_run_test_commands_success(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("go_agent.test_runner.subprocess.run") as run_mock:
        run_mock.return_value = type(
            "Completed",
            (),
            {"returncode": 0, "stdout": "ok", "stderr": ""},
        )()
        result = run_test_commands(
            repo_path,
            ["echo ok"],
            timeout=30,
            source="plan",
            plan_commands=["echo ok"],
        )

    assert result.passed is True
    assert result.commands[0].exit_code == 0
    assert result.commands[0].stdout == "ok"


def test_run_test_commands_failure_captures_output(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("go_agent.test_runner.subprocess.run") as run_mock:
        run_mock.return_value = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "", "stderr": "FAIL"},
        )()
        result = run_test_commands(repo_path, ["false"], timeout=30)

    assert result.passed is False
    assert result.commands[0].stderr == "FAIL"


def test_run_test_commands_timeout_raises(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("go_agent.test_runner.subprocess.run") as run_mock:
        run_mock.side_effect = __import__("subprocess").TimeoutExpired(
            "sleep 1", 1, output=b"partial out", stderr=b"partial err"
        )
        with pytest.raises(TestRunError, match="timed out") as exc_info:
            run_test_commands(repo_path, ["sleep 999"], timeout=1)

    result = exc_info.value.result
    assert result is not None
    assert result.passed is False
    assert result.commands[0].exit_code == -1
    assert result.commands[0].stdout == "partial out"
    assert result.commands[0].stderr == "partial err"


def test_write_test_result_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    from go_agent.config import clear_settings_cache

    clear_settings_cache()
    ctx = create_run_context()
    result = TestRunResult(
        passed=True,
        resolved_commands=["go test ./..."],
        source="plan",
        plan_commands=["go test ./..."],
    )
    path = write_test_result(ctx, result)
    assert path == ctx.artifact_dir / "test_result.json"
    assert path.exists()
