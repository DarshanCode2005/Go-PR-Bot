"""Tests for golangci/golangci-lint repo skill."""

from __future__ import annotations

from pathlib import Path

from go_agent.planner import FixPlan
from go_agent.skills import load_skill_text, resolve_lint_commands, resolve_test_commands

_REPO = "golangci/golangci-lint"
_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=100,
        repo=_REPO,
        files=["pkg/golinters/errcheck/errcheck.go"],
        steps=["Fix errcheck integration test"],
        test_commands=["go test ./pkg/... -count=1"],
        acceptance_criteria=["Integration test passes"],
    )


def test_golangci_lint_skill_file_exists():
    path = _SKILLS_ROOT / "golangci__golangci-lint" / "SKILL.md"
    assert path.is_file(), (
        "golangci-lint skill must exist at skills/golangci__golangci-lint/SKILL.md"
    )


def test_load_skill_text_for_golangci_lint():
    text = load_skill_text(_REPO)
    assert "github.com/golangci/golangci-lint/v2" in text
    assert "pkg/golinters" in text
    assert "GL_TEST_RUN=1" in text
    assert "main" in text


def test_resolve_test_commands_uses_golangci_lint_skill_override():
    commands, source = resolve_test_commands(_plan(), _REPO)
    assert source == "skill_override"
    assert commands == ["GL_TEST_RUN=1 go test ./... -count=1 -parallel 2"]


def test_resolve_lint_commands_uses_golangci_lint_skill_override(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".golangci.yml").write_text("version: \"2\"\n", encoding="utf-8")

    commands, source = resolve_lint_commands(_REPO, repo_path)
    assert source == "skill_override"
    assert commands == ["go vet ./...", "golangci-lint run"]
