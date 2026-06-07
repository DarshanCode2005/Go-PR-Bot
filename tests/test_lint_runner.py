"""Tests for subprocess lint runner and skill command resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from go_agent.lint_runner import (
    LintFinding,
    LintRunError,
    LintRunResult,
    format_finding,
    parse_lint_findings,
    run_lint_commands,
    write_lint_result,
)
from go_agent.run_context import create_run_context
from go_agent.skills import parse_skill_lint_commands, resolve_lint_commands

GIN_SKILL_WITH_LINT = """---
lint_commands:
  - go vet ./internal/...
---

# gin
"""


def test_resolve_lint_commands_default_vet_only(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    commands, source = resolve_lint_commands("example/other", repo_path)
    assert source == "default"
    assert commands == ["go vet ./..."]


@pytest.mark.parametrize(
    "config_name",
    (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json"),
)
def test_resolve_lint_commands_includes_golangci_when_config_and_binary(
    tmp_path, config_name
):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / config_name).write_text("run:\n  timeout: 5m\n", encoding="utf-8")

    with patch("go_agent.skills.shutil.which", return_value="/usr/bin/golangci-lint"):
        commands, source = resolve_lint_commands("example/other", repo_path)

    assert source == "default"
    assert commands == ["go vet ./...", "golangci-lint run"]


def test_resolve_lint_commands_skill_frontmatter_override(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    skill_dir = tmp_path / "skills" / "gin-gonic__gin"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(GIN_SKILL_WITH_LINT, encoding="utf-8")

    with patch("go_agent.skills._SKILLS_ROOT", tmp_path / "skills"):
        commands, source = resolve_lint_commands("gin-gonic/gin", repo_path)

    assert source == "skill_override"
    assert commands == ["go vet ./internal/..."]


def test_parse_skill_lint_commands_inline_bracket_list():
    skill = """---
lint_commands: [go vet ./..., golangci-lint run ./...]
---
"""
    commands = parse_skill_lint_commands(skill)
    assert commands == ["go vet ./...", "golangci-lint run ./..."]


def test_parse_lint_findings_go_vet_output():
    output = "./main.go:10:2: undefined: foo\n"
    findings = parse_lint_findings(output, command="go vet ./...")
    assert findings == [
        LintFinding(
            file="main.go",
            line=10,
            column=2,
            message="undefined: foo",
            command="go vet ./...",
        )
    ]


def test_parse_lint_findings_golangci_output():
    output = "pkg/handler.go:42:9: Error return value is not checked (errcheck)\n"
    findings = parse_lint_findings(output, command="golangci-lint run")
    assert findings[0].file == "pkg/handler.go"
    assert findings[0].line == 42
    assert findings[0].column == 9
    assert "errcheck" in findings[0].message


def test_format_finding_includes_file_line():
    finding = LintFinding(file="main.go", line=10, message="undefined: foo")
    assert format_finding(finding) == "main.go:10: undefined: foo"


def test_run_lint_commands_failure_populates_findings(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    stderr = "handler.go:5:1: syntax error: unexpected }\n"

    with patch("go_agent.lint_runner.subprocess.run") as run_mock:
        run_mock.return_value = type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "", "stderr": stderr},
        )()
        result = run_lint_commands(
            repo_path,
            ["go vet ./..."],
            timeout=30,
            source="default",
        )

    assert result.passed is False
    assert len(result.findings) == 1
    assert result.findings[0].file == "handler.go"
    assert result.findings[0].line == 5


def test_run_lint_commands_timeout_raises(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    with patch("go_agent.lint_runner.subprocess.run") as run_mock:
        run_mock.side_effect = __import__("subprocess").TimeoutExpired(
            "sleep 1", 1, output=b"", stderr=b"timeout"
        )
        with pytest.raises(LintRunError, match="timed out") as exc_info:
            run_lint_commands(repo_path, ["sleep 999"], timeout=1)

    result = exc_info.value.result
    assert result is not None
    assert result.passed is False
    assert result.commands[0].exit_code == -1


def test_write_lint_result_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    from go_agent.config import clear_settings_cache

    clear_settings_cache()
    ctx = create_run_context()
    result = LintRunResult(
        passed=True,
        resolved_commands=["go vet ./..."],
        source="default",
    )
    path = write_lint_result(ctx, result)
    assert path == ctx.artifact_dir / "lint_result.json"
    assert path.exists()
