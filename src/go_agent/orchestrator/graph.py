"""LangGraph workflow builder — implement, validation, and full closed-loop graphs."""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from go_agent.config import Settings, get_settings
from go_agent.orchestrator import nodes
from go_agent.orchestrator.state import AgentState

IMPLEMENT_NODE_NAMES: tuple[str, ...] = ("plan", "code", "integrate")
VALIDATION_NODE_NAMES: tuple[str, ...] = (*IMPLEMENT_NODE_NAMES, "test", "lint")
GRAPH_NODE_NAMES: tuple[str, ...] = (*VALIDATION_NODE_NAMES, "fix", "review", "pr")

_NODE_FUNCS = {
    "plan": nodes.plan_node,
    "code": nodes.code_node,
    "integrate": nodes.integrate_node,
    "test": nodes.test_node,
    "lint": nodes.lint_node,
    "fix": nodes.fix_node,
    "review": nodes.review_node,
    "pr": nodes.pr_node,
}


def route_after_test_validation(state: AgentState) -> str:
    """Route from test to lint when tests passed, otherwise end validation."""
    if (state.get("test_result") or {}).get("passed"):
        return "lint"
    return END


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


def _resolve_graph_mode(
    *,
    include_test: bool | None,
    include_closed_loop: bool | None,
    implement_only: bool | None,
) -> tuple[bool, bool]:
    if include_test is not None or include_closed_loop is not None:
        return include_test or False, include_closed_loop or False
    if implement_only is False:
        return True, True
    if implement_only is True:
        return False, False
    return False, False


def build_graph(
    *,
    include_test: bool | None = None,
    include_closed_loop: bool | None = None,
    implement_only: bool | None = None,
    max_fix_iterations: int | None = None,
    settings: Settings | None = None,
) -> StateGraph:
    """Build the orchestrator StateGraph (not yet compiled)."""
    settings = settings or get_settings()
    cap = max_fix_iterations if max_fix_iterations is not None else settings.max_fix_iterations
    test_enabled, closed_loop = _resolve_graph_mode(
        include_test=include_test,
        include_closed_loop=include_closed_loop,
        implement_only=implement_only,
    )

    if closed_loop:
        node_names = GRAPH_NODE_NAMES
    elif test_enabled:
        node_names = VALIDATION_NODE_NAMES
    else:
        node_names = IMPLEMENT_NODE_NAMES

    graph: StateGraph = StateGraph(AgentState)
    for name in node_names:
        graph.add_node(name, _NODE_FUNCS[name])

    graph.set_entry_point("plan")
    graph.add_edge("plan", "code")
    graph.add_edge("code", "integrate")

    if closed_loop:
        graph.add_edge("integrate", "test")
        _add_closed_loop_tail(graph, max_fix_iterations=cap)
    elif test_enabled:
        graph.add_edge("integrate", "test")
        graph.add_conditional_edges(
            "test",
            route_after_test_validation,
            {"lint": "lint", END: END},
        )
        graph.add_edge("lint", END)
    else:
        graph.add_edge("integrate", END)

    return graph


def compile_graph(
    *,
    include_test: bool | None = None,
    include_closed_loop: bool | None = None,
    implement_only: bool | None = None,
    max_fix_iterations: int | None = None,
    settings: Settings | None = None,
):
    """Compile the orchestrator graph for invoke/stream."""
    return build_graph(
        include_test=include_test,
        include_closed_loop=include_closed_loop,
        implement_only=implement_only,
        max_fix_iterations=max_fix_iterations,
        settings=settings,
    ).compile()
