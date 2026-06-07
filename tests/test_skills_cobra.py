"""Tests for spf13/cobra repo skill."""

from __future__ import annotations

from pathlib import Path

from go_agent.planner import FixPlan
from go_agent.skills import load_skill_text, resolve_lint_commands, resolve_test_commands

_REPO = "spf13/cobra"
_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=567,
        repo=_REPO,
        files=["completions.go"],
        steps=["Fix bash completion regression"],
        test_commands=["go test ./pkg/... -count=1"],
        acceptance_criteria=["Regression test passes"],
    )


def test_cobra_skill_file_exists():
    path = _SKILLS_ROOT / "spf13__cobra" / "SKILL.md"
    assert path.is_file(), "cobra skill must exist at skills/spf13__cobra/SKILL.md"


def test_load_skill_text_for_cobra():
    text = load_skill_text(_REPO)
    assert "github.com/spf13/cobra" in text
    assert "command.go" in text
    assert "completions.go" in text
    assert "main" in text


def test_resolve_test_commands_uses_cobra_skill_override():
    commands, source = resolve_test_commands(_plan(), _REPO)
    assert source == "merged"
    assert commands == ["go test ./... -count=1"]


def test_resolve_lint_commands_uses_cobra_skill_override(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".golangci.yml").write_text("version: \"2\"\n", encoding="utf-8")

    commands, source = resolve_lint_commands(_REPO, repo_path)
    assert source == "skill_override"
    assert commands == ["go vet ./...", "golangci-lint run"]
