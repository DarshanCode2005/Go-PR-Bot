"""Tests for repo skill loading, fallback, and agent prompt injection."""

from __future__ import annotations

from pathlib import Path

from go_agent.coder import PlanSlice, build_coder_messages
from go_agent.context_builder import ContextBundle
from go_agent.github_issues import IssueContext
from go_agent.planner import build_planner_messages
from go_agent.reviewer import ReviewContext, build_review_messages
from go_agent.skills import (
    format_skill_prompt,
    load_skill_text,
    resolve_skill_path,
    skill_body_for_prompt,
)

_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
_GIN_REPO = "gin-gonic/gin"
_UNKNOWN_REPO = "example/other"
_DEFAULT_MARKER = "Default Go OSS skill"
_GIN_MARKER = "tree.go"


def _issue(repo: str = _GIN_REPO) -> IssueContext:
    return IssueContext(
        repo=repo,
        number=42,
        title="Test issue",
        body="Issue body",
        state="open",
    )


def _empty_bundle(repo: str = _GIN_REPO) -> ContextBundle:
    return ContextBundle(
        issue_number=42,
        repo=repo,
        budget_chars=80000,
        total_chars=0,
        files=[],
    )


def test_resolve_skill_path_known_repo():
    path = resolve_skill_path(_GIN_REPO)
    assert path == _SKILLS_ROOT / "gin-gonic__gin" / "SKILL.md"


def test_load_skill_text_unknown_repo_uses_default():
    text = load_skill_text(_UNKNOWN_REPO)
    assert _DEFAULT_MARKER in text


def test_resolve_skill_path_unknown_repo_uses_default():
    path = resolve_skill_path(_UNKNOWN_REPO)
    assert path == _SKILLS_ROOT / "_default" / "SKILL.md"


def test_skill_body_for_prompt_strips_frontmatter():
    skill = """---
test_commands:
  - go test ./... -count=1
---

# Repo skill body
"""
    body = skill_body_for_prompt(skill)
    assert "test_commands:" not in body
    assert "Repo skill body" in body


def test_format_skill_prompt_unknown_repo_uses_default():
    section = format_skill_prompt(_UNKNOWN_REPO)
    assert section is not None
    assert section.startswith("Repo skill notes:\n")
    assert _DEFAULT_MARKER in section


def test_build_planner_messages_includes_skill():
    messages = build_planner_messages(_issue(), _empty_bundle(), [])
    user = messages[-1]["content"]
    assert "Repo skill notes:" in user
    assert _GIN_MARKER in user
    assert "test_commands:" not in user


def test_build_planner_messages_unknown_repo_uses_default():
    messages = build_planner_messages(
        _issue(_UNKNOWN_REPO),
        _empty_bundle(_UNKNOWN_REPO),
        [],
    )
    user = messages[-1]["content"]
    assert _DEFAULT_MARKER in user


def test_build_review_messages_includes_skill():
    from go_agent.planner import FixPlan

    context = ReviewContext(
        issue=_issue(),
        plan=FixPlan(
            issue_number=42,
            repo=_GIN_REPO,
            files=["context.go"],
            steps=["Fix"],
            test_commands=["go test ./... -count=1"],
            acceptance_criteria=["pass"],
        ),
        patch_text="",
        changed_files=["context.go"],
        test_output="",
        test_passed=True,
        lint_output="",
        lint_passed=True,
        lint_findings=[],
        gofmt_diff="",
        vet_output="",
    )
    user = build_review_messages(context)[-1]["content"]
    assert "Repo skill notes:" in user
    assert _GIN_MARKER in user


def test_build_coder_messages_includes_skill():
    plan_slice = PlanSlice(
        file_path="context.go",
        steps=["Fix nil guard"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["no panic"],
    )
    user = build_coder_messages(
        _issue(),
        plan_slice,
        "package gin\n",
        None,
    )[-1]["content"]
    assert "Repo skill notes:" in user
    assert _GIN_MARKER in user
