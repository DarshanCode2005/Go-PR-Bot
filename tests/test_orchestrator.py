"""Tests for LangGraph orchestrator skeleton."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from go_agent.orchestrator import GRAPH_NODE_NAMES, compile_graph
from go_agent.orchestrator.graph import route_after_test
from go_agent.orchestrator.state import AgentState

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ARCHITECTURE = _REPO_ROOT / "docs" / "ARCHITECTURE.md"


def test_graph_compiles():
    compiled = compile_graph()
    assert compiled is not None


def test_graph_has_expected_nodes():
    compiled = compile_graph()
    node_ids = set(compiled.get_graph().nodes)
    assert node_ids == set(GRAPH_NODE_NAMES) | {"__start__", "__end__"}


def test_graph_edges():
    compiled = compile_graph()
    edges = compiled.get_graph().edges
    linear = {(e.source, e.target) for e in edges if not e.conditional}
    conditional = {(e.source, e.target) for e in edges if e.conditional}

    assert ("plan", "code") in linear
    assert ("code", "test") in linear
    assert ("fix", "code") in linear
    assert ("review", "pr") in linear
    assert ("pr", "__end__") in linear
    assert ("__start__", "plan") in linear
    assert ("test", "fix") in conditional
    assert ("test", "review") in conditional


def test_stub_invoke_happy_path():
    compiled = compile_graph()
    result = compiled.invoke({"run_id": "test-run", "iteration": 0})
    assert result["status"] == "done"
    assert result["last_node"] == "pr"
    assert result["test_result"]["passed"] is True


def test_route_after_test_fix_loop():
    state = {"test_result": {"passed": False}, "iteration": 0}
    assert route_after_test(state, max_fix_iterations=5) == "fix"


def test_route_after_test_max_iterations():
    state = {"test_result": {"passed": False}, "iteration": 5}
    assert route_after_test(state, max_fix_iterations=5) == "review"


def test_route_after_test_pass():
    state = {"test_result": {"passed": True}, "iteration": 0}
    assert route_after_test(state, max_fix_iterations=5) == "review"


def _failing_test_node(state: AgentState) -> AgentState:
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": {"passed": False, "output": "fail", "command": "go test ./..."},
    }


def test_invoke_fix_loop_visits_fix_and_code():
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {"test": _failing_test_node},
    ):
        compiled = compile_graph(max_fix_iterations=1)
        visited: list[str] = []
        initial = {"run_id": "fix-loop", "iteration": 0}
        for step in compiled.stream(initial, stream_mode="updates"):
            visited.extend(step.keys())
    assert "fix" in visited
    assert visited.count("code") >= 2


def test_invoke_max_iterations_marks_failed():
    with patch.dict(
        "go_agent.orchestrator.graph._NODE_FUNCS",
        {"test": _failing_test_node},
    ):
        compiled = compile_graph(max_fix_iterations=1)
        result = compiled.invoke({"run_id": "max-iter", "iteration": 0})
    assert result["status"] == "failed"
    assert result["last_node"] == "pr"
    assert result["iteration"] == 1


def test_architecture_lists_graph_nodes():
    assert _ARCHITECTURE.is_file(), "docs/ARCHITECTURE.md must exist"
    text = _ARCHITECTURE.read_text(encoding="utf-8")
    for name in GRAPH_NODE_NAMES:
        assert name in text, f"ARCHITECTURE.md must mention node {name!r}"
