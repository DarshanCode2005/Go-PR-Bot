"""Tests for LangGraph orchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from go_agent.coder import CoderArtifact, FilePatch
from go_agent.integrator import IntegratorResult
from go_agent.orchestrator import (
    GRAPH_NODE_NAMES,
    IMPLEMENT_NODE_NAMES,
    VALIDATION_NODE_NAMES,
    compile_graph,
)
from go_agent.orchestrator.graph import (
    route_after_lint,
    route_after_review,
    route_after_test,
    route_after_test_validation,
)
from go_agent.orchestrator.state import AgentState
from go_agent.patches import PatchResult
from go_agent.planner import FixPlan
from helpers import enable_agent_mocks, init_git_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ARCHITECTURE = _REPO_ROOT / "docs" / "ARCHITECTURE.md"


def test_graph_compiles():
    compiled = compile_graph()
    assert compiled is not None


def test_graph_compiles_full():
    compiled = compile_graph(implement_only=False)
    assert compiled is not None


def test_graph_compiles_validation():
    compiled = compile_graph(include_test=True)
    assert compiled is not None


def test_validation_graph_has_expected_nodes():
    compiled = compile_graph(include_test=True)
    node_ids = set(compiled.get_graph().nodes)
    assert node_ids == set(VALIDATION_NODE_NAMES) | {"__start__", "__end__"}


def test_validation_graph_edges():
    compiled = compile_graph(include_test=True)
    edges = compiled.get_graph().edges
    linear = {(e.source, e.target) for e in edges if not e.conditional}
    conditional = {(e.source, e.target) for e in edges if e.conditional}
    assert ("integrate", "test") in linear
    assert ("lint", "__end__") in linear
    assert ("test", "lint") in conditional
    assert ("test", "__end__") in conditional


def test_implement_graph_has_expected_nodes():
    compiled = compile_graph(implement_only=True)
    node_ids = set(compiled.get_graph().nodes)
    assert node_ids == set(IMPLEMENT_NODE_NAMES) | {"__start__", "__end__"}


def test_full_graph_has_expected_nodes():
    compiled = compile_graph(implement_only=False)
    node_ids = set(compiled.get_graph().nodes)
    assert node_ids == set(GRAPH_NODE_NAMES) | {"__start__", "__end__"}


def test_implement_graph_edges():
    compiled = compile_graph(implement_only=True)
    edges = compiled.get_graph().edges
    linear = {(e.source, e.target) for e in edges if not e.conditional}

    assert ("plan", "code") in linear
    assert ("code", "integrate") in linear
    assert ("integrate", "__end__") in linear
    assert ("__start__", "plan") in linear


def test_full_graph_edges():
    compiled = compile_graph(implement_only=False)
    edges = compiled.get_graph().edges
    linear = {(e.source, e.target) for e in edges if not e.conditional}
    conditional = {(e.source, e.target) for e in edges if e.conditional}

    assert ("code", "integrate") in linear
    assert ("integrate", "test") in linear
    assert ("fix", "code") in linear
    assert ("pr", "__end__") in linear
    assert ("test", "fix") in conditional
    assert ("test", "lint") in conditional
    assert ("test", "review") in conditional
    assert ("lint", "fix") in conditional
    assert ("lint", "review") in conditional
    assert ("review", "fix") in conditional
    assert ("review", "pr") in conditional


def _base_sha(repo_path: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_implement_invoke_happy_path(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-1"
    artifact_dir.mkdir(parents=True)
    enable_agent_mocks(monkeypatch)

    compiled = compile_graph(implement_only=True)
    result = compiled.invoke(
        {
            "run_id": "run-1",
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "artifact_dir": str(artifact_dir),
            "repo_path": str(repo_path),
            "scope_hints": [],
            "issue_context": {
                "repo": "gin-gonic/gin",
                "number": 1,
                "title": "Update readme",
                "state": "open",
            },
            "context_bundle": {
                "repo": "gin-gonic/gin",
                "issue_number": 1,
                "files": [],
                "total_chars": 0,
                "budget_chars": 12000,
            },
            "branch_meta": {"base_sha": _base_sha(repo_path), "branch_name": "agent/issue-1"},
            "iteration": 0,
            "stop_after_integrate": True,
        }
    )
    assert result["last_node"] == "integrate"
    assert result.get("patch_applied") is True
    assert (artifact_dir / "plan.json").exists()
    assert (artifact_dir / "proposed.patch").exists()
    assert (artifact_dir / "integrator_meta.json").exists()
    assert (artifact_dir / "changes.patch").exists()


def test_validation_invoke_happy_path(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-val"
    artifact_dir.mkdir(parents=True)
    enable_agent_mocks(monkeypatch)

    compiled = compile_graph(include_test=True)
    result = compiled.invoke(
        {
            "run_id": "run-val",
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "artifact_dir": str(artifact_dir),
            "repo_path": str(repo_path),
            "scope_hints": [],
            "issue_context": {
                "repo": "gin-gonic/gin",
                "number": 1,
                "title": "Update readme",
                "state": "open",
            },
            "context_bundle": {
                "repo": "gin-gonic/gin",
                "issue_number": 1,
                "files": [],
                "total_chars": 0,
                "budget_chars": 12000,
            },
            "branch_meta": {"base_sha": _base_sha(repo_path), "branch_name": "agent/issue-1"},
            "iteration": 0,
        }
    )
    assert result["last_node"] == "lint"
    assert result.get("test_result", {}).get("passed") is True
    assert result.get("lint_result", {}).get("passed") is True
    assert (artifact_dir / "test_result.json").exists()
    assert (artifact_dir / "lint_result.json").exists()


def test_route_after_test_validation_pass():
    state = {"test_result": {"passed": True}}
    assert route_after_test_validation(state) == "lint"


def test_route_after_test_validation_fail():
    from langgraph.graph import END

    state = {"test_result": {"passed": False}}
    assert route_after_test_validation(state) == END


def test_validation_skips_lint_when_tests_fail(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-fail"
    artifact_dir.mkdir(parents=True)
    enable_agent_mocks(monkeypatch)

    def failing_run_tests(*args, **kwargs):
        from go_agent.test_runner import CommandResult, TestRunResult

        command = "go test ./... -count=1"
        return TestRunResult(
            passed=False,
            commands=[
                CommandResult(
                    command=command,
                    exit_code=1,
                    passed=False,
                    stdout="",
                    stderr="FAIL",
                    duration_seconds=0.1,
                )
            ],
            resolved_commands=[command],
            source="plan",
            plan_commands=[command],
        )

    monkeypatch.setattr("go_agent.orchestrator.nodes.run_tests", failing_run_tests)

    compiled = compile_graph(include_test=True)
    result = compiled.invoke(
        {
            "run_id": "run-fail",
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "artifact_dir": str(artifact_dir),
            "repo_path": str(repo_path),
            "scope_hints": [],
            "issue_context": {
                "repo": "gin-gonic/gin",
                "number": 1,
                "title": "Update readme",
                "state": "open",
            },
            "context_bundle": {
                "repo": "gin-gonic/gin",
                "issue_number": 1,
                "files": [],
                "total_chars": 0,
                "budget_chars": 12000,
            },
            "branch_meta": {"base_sha": _base_sha(repo_path), "branch_name": "agent/issue-1"},
            "iteration": 0,
        }
    )
    assert result["last_node"] == "test"
    assert result.get("test_result", {}).get("passed") is False
    assert "lint_result" not in result
    assert (artifact_dir / "test_result.json").exists()
    assert not (artifact_dir / "lint_result.json").exists()


def test_route_after_lint_pass():
    state = {"lint_result": {"passed": True}, "iteration": 0}
    assert route_after_lint(state, max_fix_iterations=5) == "review"


def test_route_after_lint_fix_loop():
    state = {"lint_result": {"passed": False}, "iteration": 0}
    assert route_after_lint(state, max_fix_iterations=5) == "fix"


def test_route_after_lint_max_iterations():
    state = {"lint_result": {"passed": False}, "iteration": 5}
    assert route_after_lint(state, max_fix_iterations=5) == "review"


def test_route_after_test_fix_loop():
    state = {"test_result": {"passed": False}, "iteration": 0}
    assert route_after_test(state, max_fix_iterations=5) == "fix"


def test_route_after_test_max_iterations():
    state = {"test_result": {"passed": False}, "iteration": 5}
    assert route_after_test(state, max_fix_iterations=5) == "review"


def test_route_after_test_pass():
    state = {"test_result": {"passed": True}, "iteration": 0}
    assert route_after_test(state, max_fix_iterations=5) == "lint"


def test_route_after_review_approve():
    state = {"review": {"decision": "approve"}, "review_round": 0}
    assert route_after_review(state, max_review_rounds=1) == "pr"


def test_route_after_review_request_changes_retry():
    state = {"review": {"decision": "request_changes"}, "review_round": 0}
    assert route_after_review(state, max_review_rounds=1) == "fix"


def test_route_after_review_request_changes_exhausted():
    state = {"review": {"decision": "request_changes"}, "review_round": 1}
    assert route_after_review(state, max_review_rounds=1) == "pr"


def test_route_after_review_reject():
    state = {"review": {"decision": "reject"}, "review_round": 0}
    assert route_after_review(state, max_review_rounds=1) == "pr"


def _failing_test_node(state: AgentState) -> AgentState:
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": {"passed": False, "output": "fail", "command": "go test ./..."},
    }


def _stub_plan_node(state: AgentState) -> AgentState:
    return {
        "status": "planning",
        "last_node": "plan",
        "fix_plan": {
            "issue_number": 1,
            "repo": "gin-gonic/gin",
            "files": ["README.md"],
            "steps": ["stub"],
            "test_commands": ["go test ./..."],
            "acceptance_criteria": ["pass"],
        },
    }


def _stub_code_node(state: AgentState) -> AgentState:
    return {"status": "coding", "last_node": "code"}


def _stub_integrate_node(state: AgentState) -> AgentState:
    return {"status": "integrating", "last_node": "integrate"}


def _stub_fix_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration", 0) + 1
    review = state.get("review") or {}
    result: AgentState = {
        "status": "fixing",
        "last_node": "fix",
        "iteration": iteration,
    }
    if review.get("decision") == "request_changes":
        result["review_round"] = state.get("review_round", 0) + 1
    return result


def _passing_test_node(state: AgentState) -> AgentState:
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": {"passed": True, "output": "ok", "command": "go test ./..."},
    }


def _passing_lint_node(state: AgentState) -> AgentState:
    return {
        "status": "linting",
        "last_node": "lint",
        "lint_result": {"passed": True, "output": "ok", "findings": []},
    }


def _closed_loop_initial_state(*, run_id: str = "review-loop") -> AgentState:
    return {
        "run_id": run_id,
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": f"/tmp/{run_id}",
        "repo_path": "/tmp/repo",
        "scope_hints": [],
        "issue_context": {
            "repo": "gin-gonic/gin",
            "number": 1,
            "title": "Update readme",
            "state": "open",
        },
        "context_bundle": {
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "files": [],
            "total_chars": 0,
            "budget_chars": 12000,
        },
        "branch_meta": {"base_sha": "abc", "branch_name": "agent/issue-1"},
        "iteration": 0,
        "review_round": 0,
    }


def test_review_fix_loop_retries_then_approves(tmp_path, monkeypatch):
    from go_agent.reviewer import ReviewChecklist, ReviewResult

    artifact_dir = tmp_path / "artifacts" / "review-loop"
    artifact_dir.mkdir(parents=True)
    request_review = ReviewResult(
        decision="request_changes",
        comments=["Improve error message in foo.go"],
        checklist=ReviewChecklist(style=False),
    )
    approve_review = ReviewResult(
        decision="approve",
        comments=["Looks good"],
        checklist=ReviewChecklist(
            acceptance_criteria=True,
            tests=True,
            api_breaks=True,
            style=True,
            error_messages=True,
        ),
    )
    calls = {"count": 0}

    def mock_build_review(*args, **kwargs):
        calls["count"] += 1
        return request_review if calls["count"] == 1 else approve_review

    monkeypatch.setattr("go_agent.orchestrator.nodes.build_review", mock_build_review)
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {
            "plan": _stub_plan_node,
            "code": _stub_code_node,
            "integrate": _stub_integrate_node,
            "test": _passing_test_node,
            "lint": _passing_lint_node,
            "fix": _stub_fix_node,
        },
    ):
        result = compile_graph(include_closed_loop=True, max_review_rounds=1).invoke(
            {**_closed_loop_initial_state(), "artifact_dir": str(artifact_dir)}
        )

    assert result["status"] == "done"
    assert result["last_node"] == "pr"
    assert result["review"]["decision"] == "approve"
    assert calls["count"] == 2
    assert result.get("review_round") == 1


def test_review_fix_loop_escalates_on_second_request_changes(tmp_path, monkeypatch):
    from go_agent.reviewer import ReviewChecklist, ReviewResult

    artifact_dir = tmp_path / "artifacts" / "review-fail"
    artifact_dir.mkdir(parents=True)
    request_review = ReviewResult(
        decision="request_changes",
        comments=["Fix foo.go formatting"],
        checklist=ReviewChecklist(style=False),
    )
    calls = {"count": 0}

    def mock_build_review(*args, **kwargs):
        calls["count"] += 1
        return request_review

    monkeypatch.setattr("go_agent.orchestrator.nodes.build_review", mock_build_review)
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {
            "plan": _stub_plan_node,
            "code": _stub_code_node,
            "integrate": _stub_integrate_node,
            "test": _passing_test_node,
            "lint": _passing_lint_node,
            "fix": _stub_fix_node,
        },
    ):
        compiled = compile_graph(include_closed_loop=True, max_review_rounds=1)
        result = compiled.invoke(
            {**_closed_loop_initial_state(run_id="review-fail"), "artifact_dir": str(artifact_dir)}
        )

    assert result["status"] == "failed"
    assert result["last_node"] == "pr"
    assert result["review"]["decision"] == "request_changes"
    assert calls["count"] == 2
    assert (artifact_dir / "review.json").exists()


def test_full_invoke_fix_loop_visits_fix_and_code():
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {
            "plan": _stub_plan_node,
            "code": _stub_code_node,
            "integrate": _stub_integrate_node,
            "test": _failing_test_node,
            "fix": _stub_fix_node,
        },
    ):
        compiled = compile_graph(implement_only=False, max_fix_iterations=1)
        visited: list[str] = []
        initial = {"run_id": "fix-loop", "iteration": 0}
        for step in compiled.stream(initial, stream_mode="updates"):
            visited.extend(step.keys())
    assert "fix" in visited
    assert visited.count("code") >= 2


def test_full_invoke_max_iterations_marks_failed():
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {
            "plan": _stub_plan_node,
            "code": _stub_code_node,
            "integrate": _stub_integrate_node,
            "test": _failing_test_node,
            "fix": _stub_fix_node,
        },
    ):
        compiled = compile_graph(implement_only=False, max_fix_iterations=1)
        result = compiled.invoke({"run_id": "max-iter", "iteration": 0})
    assert result["status"] == "failed"
    assert result["last_node"] == "pr"
    assert result["iteration"] == 1


def test_plan_code_integrate_nodes_with_mocks(tmp_path):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-mock"
    artifact_dir.mkdir(parents=True)
    base_sha = _base_sha(repo_path)

    fix_plan = FixPlan(
        issue_number=1,
        repo="gin-gonic/gin",
        files=["README.md"],
        steps=["Update readme"],
        test_commands=["go test ./..."],
        acceptance_criteria=["Tests pass"],
    )
    file_patch = FilePatch(
        path="README.md",
        format="unified_diff",
        patch="--- a/README.md\n+++ b/README.md\n",
    )
    coder_artifact = CoderArtifact(
        issue_number=1,
        repo="gin-gonic/gin",
        files=[file_patch],
        combined_patch=file_patch.patch,
        execution_waves=[["README.md"]],
    )
    integrator_result = IntegratorResult(
        resolved_patch=file_patch.patch,
        conflicts=[],
        files_touched=["README.md"],
    )
    patch_result = PatchResult(
        commit_sha="abc123",
        commit_message="fix: update (fixes #1)",
        changes_patch_path=artifact_dir / "changes.patch",
    )

    initial_state = {
        "run_id": "run-mock",
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "scope_hints": [],
        "issue_context": {
            "repo": "gin-gonic/gin",
            "number": 1,
            "title": "Update readme",
            "state": "open",
        },
        "context_bundle": {
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "files": [],
            "total_chars": 0,
            "budget_chars": 12000,
        },
        "branch_meta": {"base_sha": base_sha, "branch_name": "agent/issue-1"},
        "iteration": 0,
    }

    with (
        patch("go_agent.orchestrator.nodes.build_fix_plan", return_value=fix_plan),
        patch("go_agent.orchestrator.nodes.write_plan"),
        patch("go_agent.orchestrator.nodes.build_proposed_patch", return_value=coder_artifact),
        patch("go_agent.orchestrator.nodes.write_coder_artifact") as write_coder,
        patch(
            "go_agent.orchestrator.nodes.integrate_file_patches",
            return_value=integrator_result,
        ),
        patch("go_agent.orchestrator.nodes.write_integrator_artifact"),
        patch("go_agent.orchestrator.nodes.apply_patch_and_commit", return_value=patch_result),
    ):
        write_coder.side_effect = lambda ctx, artifact: (
            (ctx.artifact_dir / "coder_meta.json").write_text(
                artifact.model_dump_json(indent=2),
                encoding="utf-8",
            )
        )
        result = compile_graph(implement_only=True).invoke(initial_state)

    assert result["patch_applied"] is True
    assert result["last_node"] == "integrate"
    assert result["commit_sha"] == "abc123"


def _langgraph_architecture_section(text: str) -> str:
    marker = "## LangGraph orchestrator (code)"
    assert marker in text, "ARCHITECTURE.md must document the LangGraph orchestrator"
    return text.split(marker, 1)[1].split("\n---", 1)[0]


def test_architecture_lists_graph_nodes():
    assert _ARCHITECTURE.is_file(), "docs/ARCHITECTURE.md must exist"
    section = _langgraph_architecture_section(_ARCHITECTURE.read_text(encoding="utf-8"))

    for name in IMPLEMENT_NODE_NAMES:
        assert name in section, f"LangGraph section must mention wired node {name!r}"

    for edge in (
        "plan --> code",
        "code --> integrate",
        "integrate --> test",
        "fix --> code",
        'review -->|"request_changes',
        "review -->|approve or exhausted| pr",
    ):
        assert edge in section, f"LangGraph mermaid must include edge {edge!r}"
