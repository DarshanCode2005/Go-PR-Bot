"""Tests for review agent and format/lint review context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.lint_runner import LintFinding
from go_agent.llm_client import set_completion_transport
from go_agent.planner import FixPlan
from go_agent.reviewer import (
    ReviewError,
    ReviewResult,
    build_review,
    build_review_context,
    build_review_messages,
    extract_vet_output,
    parse_changed_files,
    run_gofmt_diff,
    write_review,
)
from go_agent.run_context import create_run_context
from helpers import init_git_repo

VALID_REVIEW_JSON = (
    '{"decision":"approve","comments":["Tests cover nil context in context_test.go"],'
    '"checklist":{"acceptance_criteria":true,"tests":true,"api_breaks":true,'
    '"style":true,"error_messages":true}}'
)

VET_CITATION_REVIEW_JSON = (
    '{"decision":"request_changes","comments":["Fix vet issue in foo.go:12: undefined variable"],'
    '"checklist":{"acceptance_criteria":true,"tests":true,"api_breaks":true,'
    '"style":false,"error_messages":true}}'
)


class RecordingTransport:
    def __init__(self, responses: list[str | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.index = 0

    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        self.calls.append({"model": model, "messages": messages, "temperature": temperature})
        result = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _reset_state():
    clear_settings_cache()
    set_completion_transport(None)
    yield
    set_completion_transport(None)
    clear_settings_cache()


def _issue() -> IssueContext:
    return IssueContext(
        repo="gin-gonic/gin",
        number=42,
        title="Panic in BindJSON when context is nil",
        body="BindJSON should return an error instead of panicking.",
        state="open",
    )


def _plan() -> FixPlan:
    return FixPlan(
        issue_number=42,
        repo="gin-gonic/gin",
        files=["context.go"],
        steps=["Add nil guard in BindJSON"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["BindJSON handles nil context without panic"],
    )


def test_parse_changed_files_from_patch():
    patch = "diff --git a/pkg/foo.go b/pkg/foo.go\n--- a/pkg/foo.go\n+++ b/pkg/foo.go\n"
    assert parse_changed_files(patch) == ["pkg/foo.go"]


def test_run_gofmt_diff_reports_misformatted_file(tmp_path):
    repo_path = tmp_path / "repo"
    init_git_repo(
        repo_path,
        files={"main.go": "package main\nfunc main(){fmt.Println(1)}\n"},
    )
    diff = run_gofmt_diff(repo_path, ["main.go"])
    assert "main.go" in diff or diff == ""


def test_extract_vet_output_includes_findings():
    lint_result = {
        "output": "$ go vet ./...\n./foo.go:12:2: undefined: bar",
        "findings": [
            LintFinding(file="foo.go", line=12, message="undefined: bar").model_dump()
        ],
    }
    output = extract_vet_output(lint_result)
    assert "foo.go:12" in output


def test_build_review_messages_include_ac_and_gofmt(tmp_path):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"main.go": "package main\n\nfunc main() {\n}\n"})
    context = build_review_context(
        _issue(),
        _plan(),
        repo_path=repo_path,
        patch_text="diff --git a/context.go b/context.go\n",
        test_result={"passed": True, "output": "ok"},
        lint_result={"passed": True, "output": "ok", "findings": []},
    )
    messages = build_review_messages(context)
    user = messages[-1]["content"]
    assert "Acceptance criteria" in user
    assert "Format check (gofmt -d" in user
    assert "Vet / lint output" in user
    assert "BindJSON handles nil context without panic" in user


def test_build_review_happy_path(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport([VALID_REVIEW_JSON])
    set_completion_transport(transport)
    review = build_review(
        _issue(),
        _plan(),
        repo_path=Path("/tmp/repo"),
        patch_text="diff --git a/context.go b/context.go\n",
        test_result={"passed": True, "output": "ok"},
        lint_result={"passed": True, "output": "ok", "findings": []},
        settings=Settings(),
    )
    assert review.decision == "approve"
    assert review.approved is True
    assert review.comments
    assert transport.calls[0]["model"]


def test_build_review_cites_vet_finding_in_comments(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport([VET_CITATION_REVIEW_JSON])
    set_completion_transport(transport)
    review = build_review(
        _issue(),
        _plan(),
        repo_path=Path("/tmp/repo"),
        patch_text="diff --git a/foo.go b/foo.go\n",
        test_result={"passed": True, "output": "ok"},
        lint_result={
            "passed": True,
            "output": "./foo.go:12:2: undefined: bar",
            "findings": [
                LintFinding(file="foo.go", line=12, message="undefined: bar").model_dump()
            ],
        },
        settings=Settings(),
    )
    assert review.decision == "request_changes"
    assert any("foo.go" in comment for comment in review.comments)


def test_build_review_messages_include_vet_finding(tmp_path):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"foo.go": "package main\n\nfunc main() {}\n"})
    context = build_review_context(
        _issue(),
        _plan(),
        repo_path=repo_path,
        patch_text="diff --git a/foo.go b/foo.go\n",
        test_result={"passed": True, "output": "ok"},
        lint_result={
            "passed": True,
            "output": "./foo.go:12:2: undefined: bar",
            "findings": [
                LintFinding(file="foo.go", line=12, message="undefined: bar").model_dump()
            ],
        },
    )
    user = build_review_messages(context)[-1]["content"]
    assert "foo.go:12" in user


def test_build_review_invalid_json_retries(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport(["not json", VALID_REVIEW_JSON])
    set_completion_transport(transport)
    review = build_review(
        _issue(),
        _plan(),
        repo_path=Path("/tmp/repo"),
        patch_text="",
        test_result={"passed": True, "output": "ok"},
        lint_result={"passed": True, "output": "ok", "findings": []},
        settings=Settings(),
    )
    assert review.decision == "approve"
    assert len(transport.calls) == 2


def test_build_review_raises_without_api_key():
    with pytest.raises(ReviewError, match="LLM API key"):
        build_review(
            _issue(),
            _plan(),
            repo_path=Path("/tmp/repo"),
            patch_text="",
            test_result={"passed": True, "output": "ok"},
            lint_result={"passed": True, "output": "ok", "findings": []},
            settings=Settings(),
        )


def test_write_review_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    ctx = create_run_context()
    review = ReviewResult.model_validate_json(VALID_REVIEW_JSON)
    path = write_review(ctx, review)
    assert path.name == "review.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["decision"] == "approve"
    assert payload["comments"]


def test_review_node_writes_review_json_on_approve(tmp_path, monkeypatch):
    from go_agent.orchestrator.nodes import review_node
    from go_agent.orchestrator.state import AgentState

    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-review"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()

    patch_path = artifact_dir / "changes.patch"
    patch_path.write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    approved = ReviewResult.model_validate_json(VALID_REVIEW_JSON)

    initial_state: AgentState = {
        "run_id": "run-review",
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "iteration": 0,
        "test_result": {"passed": True, "output": "ok"},
        "lint_result": {"passed": True, "output": "ok", "findings": []},
        "changes_patch_path": str(patch_path),
        "issue_context": _issue().model_dump(),
        "fix_plan": _plan().model_dump(),
    }

    with patch("go_agent.orchestrator.nodes.build_review", return_value=approved):
        result = review_node(initial_state)

    assert result["status"] == "reviewing"
    assert result["review"]["decision"] == "approve"
    assert (artifact_dir / "review.json").exists()


def test_review_node_request_changes_allows_retry(tmp_path, monkeypatch):
    from go_agent.orchestrator.nodes import review_node
    from go_agent.orchestrator.state import AgentState
    from go_agent.reviewer import ReviewChecklist

    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    artifact_dir = tmp_path / "artifacts" / "run-retry"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()

    patch_path = artifact_dir / "changes.patch"
    patch_path.write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    review = ReviewResult(
        decision="request_changes",
        comments=["foo.go:12 needs formatting fix per gofmt -d"],
        checklist=ReviewChecklist(style=False),
    )

    initial_state: AgentState = {
        "run_id": "run-retry",
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "iteration": 0,
        "review_round": 0,
        "test_result": {"passed": True, "output": "ok"},
        "lint_result": {"passed": True, "output": "ok", "findings": []},
        "changes_patch_path": str(patch_path),
        "issue_context": _issue().model_dump(),
        "fix_plan": _plan().model_dump(),
    }

    with patch("go_agent.orchestrator.nodes.build_review", return_value=review):
        result = review_node(initial_state)

    assert result["status"] == "reviewing"
    assert result["review"]["decision"] == "request_changes"


def test_review_node_request_changes_exhausted_sets_failed(tmp_path, monkeypatch):
    from go_agent.orchestrator.nodes import review_node
    from go_agent.orchestrator.state import AgentState
    from go_agent.reviewer import ReviewChecklist

    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    artifact_dir = tmp_path / "artifacts" / "run-exhausted"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()

    patch_path = artifact_dir / "changes.patch"
    patch_path.write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    review = ReviewResult(
        decision="request_changes",
        comments=["Still needs changes in foo.go:12"],
        checklist=ReviewChecklist(style=False),
    )

    initial_state: AgentState = {
        "run_id": "run-exhausted",
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "iteration": 0,
        "review_round": 1,
        "test_result": {"passed": True, "output": "ok"},
        "lint_result": {"passed": True, "output": "ok", "findings": []},
        "changes_patch_path": str(patch_path),
        "issue_context": _issue().model_dump(),
        "fix_plan": _plan().model_dump(),
    }

    with patch("go_agent.orchestrator.nodes.build_review", return_value=review):
        result = review_node(initial_state)

    assert result["status"] == "failed"
    assert (artifact_dir / "review.json").exists()


def test_review_node_reject_sets_failed_status(tmp_path, monkeypatch):
    from go_agent.orchestrator.nodes import review_node
    from go_agent.orchestrator.state import AgentState
    from go_agent.reviewer import ReviewChecklist

    repo_path = tmp_path / "repo"
    init_git_repo(repo_path)
    artifact_dir = tmp_path / "artifacts" / "run-reject"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()

    patch_path = artifact_dir / "changes.patch"
    patch_path.write_text("diff --git a/README.md b/README.md\n", encoding="utf-8")

    rejected = ReviewResult(
        decision="reject",
        comments=["Change is too risky for this issue"],
        checklist=ReviewChecklist(api_breaks=False),
    )

    initial_state: AgentState = {
        "run_id": "run-reject",
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "iteration": 0,
        "test_result": {"passed": True, "output": "ok"},
        "lint_result": {"passed": True, "output": "ok", "findings": []},
        "changes_patch_path": str(patch_path),
        "issue_context": _issue().model_dump(),
        "fix_plan": _plan().model_dump(),
    }

    with patch("go_agent.orchestrator.nodes.build_review", return_value=rejected):
        result = review_node(initial_state)

    assert result["status"] == "failed"
    assert result["review"]["decision"] == "reject"
    assert (artifact_dir / "review.json").exists()
