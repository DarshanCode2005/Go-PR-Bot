"""LangGraph workflow builder — stub closed loop (plan → code → test → fix → review → pr)."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from go_agent.config import Settings, get_settings
from go_agent.orchestrator import nodes
from go_agent.orchestrator.state import AgentState

GRAPH_NODE_NAMES: tuple[str, ...] = ("plan", "code", "test", "fix", "review", "pr")

_NODE_FUNCS = {
    "plan": nodes.plan_node,
    "code": nodes.code_node,
    "test": nodes.test_node,
    "fix": nodes.fix_node,
    "review": nodes.review_node,
    "pr": nodes.pr_node,
}


def route_after_test(state: AgentState, max_fix_iterations: int) -> str:
    """Route from test to fix (retry) or review (pass or max iterations)."""
    test_result = state.get("test_result") or {}
    if test_result.get("passed"):
        return "review"
    iteration = state.get("iteration", 0)
    if iteration < max_fix_iterations:
        return "fix"
    return "review"


def build_graph(*, max_fix_iterations: int | None = None, settings: Settings | None = None) -> StateGraph:
    """Build the orchestrator StateGraph (not yet compiled)."""
    settings = settings or get_settings()
    cap = max_fix_iterations if max_fix_iterations is not None else settings.max_fix_iterations

    graph: StateGraph = StateGraph(AgentState)
    for name in GRAPH_NODE_NAMES:
        graph.add_node(name, _NODE_FUNCS[name])

    graph.set_entry_point("plan")
    graph.add_edge("plan", "code")
    graph.add_edge("code", "test")
    graph.add_conditional_edges(
        "test",
        lambda state: route_after_test(state, cap),
        {"fix": "fix", "review": "review"},
    )
    graph.add_edge("fix", "code")
    graph.add_edge("review", "pr")
    graph.add_edge("pr", END)
    return graph


def compile_graph(
    *,
    max_fix_iterations: int | None = None,
    settings: Settings | None = None,
):
    """Compile the orchestrator graph for invoke/stream."""
    return build_graph(max_fix_iterations=max_fix_iterations, settings=settings).compile()
