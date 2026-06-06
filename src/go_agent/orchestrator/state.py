"""LangGraph orchestrator state — TypedDict channel schema and Pydantic sub-models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

AgentStatus = Literal[
    "planning",
    "coding",
    "testing",
    "fixing",
    "reviewing",
    "done",
    "failed",
]


class TestResult(BaseModel):
    """Result of a validation subprocess (stub until test runner is wired)."""

    passed: bool = False
    output: str = ""
    command: str = ""


class ReviewResult(BaseModel):
    """Reviewer output (stub until review agent is wired)."""

    approved: bool = False
    comments: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes."""

    run_id: str
    repo: str
    issue_number: int
    status: AgentStatus
    iteration: int
    last_node: str
    test_result: dict[str, Any]
    review: dict[str, Any]
    error: str | None
