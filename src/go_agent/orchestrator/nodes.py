"""Stub LangGraph node functions — real agent wiring deferred to a later issue."""

from __future__ import annotations

from go_agent.orchestrator.state import AgentState, ReviewResult, TestResult


def plan_node(state: AgentState) -> AgentState:
    return {
        "status": "planning",
        "last_node": "plan",
    }


def code_node(state: AgentState) -> AgentState:
    return {
        "status": "coding",
        "last_node": "code",
    }


def test_node(state: AgentState) -> AgentState:
    # Stub always marks tests passed. Overwrite test_result on every call so a
    # prior failure does not stick after a successful fix.
    result = TestResult(passed=True, output="stub: tests passed", command="stub")
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": result.model_dump(),
    }


def fix_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration", 0) + 1
    return {
        "status": "fixing",
        "last_node": "fix",
        "iteration": iteration,
    }


def review_node(state: AgentState) -> AgentState:
    test_result = state.get("test_result") or {}
    passed = bool(test_result.get("passed"))
    if passed:
        review = ReviewResult(approved=True, comments=["stub: approved"])
        return {
            "status": "reviewing",
            "last_node": "review",
            "review": review.model_dump(),
        }
    review = ReviewResult(
        approved=False,
        comments=["stub: tests still failing after max iterations"],
    )
    return {
        "status": "failed",
        "last_node": "review",
        "review": review.model_dump(),
    }


def pr_node(state: AgentState) -> AgentState:
    status = state.get("status")
    final_status = "done" if status != "failed" else "failed"
    return {
        "status": final_status,
        "last_node": "pr",
    }
