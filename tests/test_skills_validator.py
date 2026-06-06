"""Tests for go-playground/validator repo skill."""

from __future__ import annotations

from pathlib import Path

from go_agent.planner import FixPlan
from go_agent.skills import load_skill_text, resolve_lint_commands, resolve_test_commands

_REPO = "go-playground/validator"
_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=32,
        repo=_REPO,
        files=["baked_in.go"],
        steps=["Fix required tag for empty string"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Regression test passes"],
    )


def test_validator_skill_file_exists():
    path = _SKILLS_ROOT / "go-playground__validator" / "SKILL.md"
    assert path.is_file(), "validator skill must exist at skills/go-playground__validator/SKILL.md"


def test_load_skill_text_for_validator():
    text = load_skill_text(_REPO)
    assert "github.com/go-playground/validator/v10" in text
    assert "baked_in.go" in text
    assert "validator_test.go" in text


def test_resolve_test_commands_uses_validator_skill_override():
    commands, source = resolve_test_commands(_plan(), _REPO)
    assert source == "skill_override"
    assert commands == ["go test -race ./... -count=1"]


def test_resolve_lint_commands_uses_validator_skill_override(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".golangci.yaml").write_text("version: \"2\"\n", encoding="utf-8")

    commands, source = resolve_lint_commands(_REPO, repo_path)
    assert source == "skill_override"
    assert commands == ["go vet ./...", "golangci-lint run"]
