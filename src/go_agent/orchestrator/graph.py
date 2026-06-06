"""LangGraph workflow builder — implement phase and full closed-loop graphs."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from go_agent.config import Settings, get_settings
from go_agent.orchestrator import nodes
from go_agent.orchestrator.state import AgentState

IMPLEMENT_NODE_NAMES: tuple[str, ...] = ("plan", "code", "integrate")
GRAPH_NODE_NAMES: tuple[str, ...] = (*IMPLEMENT_NODE_NAMES, "test", "fix", "review", "pr")

_NODE_FUNCS = {
    "plan": nodes.plan_node,
    "code": nodes.code_node,
    "integrate": nodes.integrate_node,
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


def _add_closed_loop_tail(
    graph: StateGraph,
    *,
    max_fix_iterations: int,
) -> None:
    graph.add_conditional_edges(
        "test",
        lambda state: route_after_test(state, max_fix_iterations),
        {"fix": "fix", "review": "review"},
    )
    graph.add_edge("fix", "code")
    graph.add_edge("review", "pr")
    graph.add_edge("pr", END)


def build_graph(
    *,
    implement_only: bool = True,
    max_fix_iterations: int | None = None,
    settings: Settings | None = None,
) -> StateGraph:
    """Build the orchestrator StateGraph (not yet compiled)."""
    settings = settings or get_settings()
    cap = max_fix_iterations if max_fix_iterations is not None else settings.max_fix_iterations

    node_names = IMPLEMENT_NODE_NAMES if implement_only else GRAPH_NODE_NAMES
    graph: StateGraph = StateGraph(AgentState)
    for name in node_names:
        graph.add_node(name, _NODE_FUNCS[name])

    graph.set_entry_point("plan")
    graph.add_edge("plan", "code")
    graph.add_edge("code", "integrate")

    if implement_only:
        graph.add_edge("integrate", END)
    else:
        graph.add_edge("integrate", "test")
        _add_closed_loop_tail(graph, max_fix_iterations=cap)

    return graph


def compile_graph(
    *,
    implement_only: bool = True,
    max_fix_iterations: int | None = None,
    settings: Settings | None = None,
):
    """Compile the orchestrator graph for invoke/stream."""
    return build_graph(
        implement_only=implement_only,
        max_fix_iterations=max_fix_iterations,
        settings=settings,
    ).compile()
