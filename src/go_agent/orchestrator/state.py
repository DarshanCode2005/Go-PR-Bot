"""LangGraph orchestrator state — TypedDict channel schema and Pydantic sub-models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict

AgentStatus = Literal[
    "planning",
    "coding",
    "integrating",
    "testing",
    "fixing",
    "reviewing",
    "shipping",
    "done",
    "failed",
]


class TestResult(BaseModel):
    """Result of a validation subprocess."""

    passed: bool = False
    exit_code: int = 0
    output: str = ""
    command: str = ""
    commands: list[str] = Field(default_factory=list)
    source: str = "plan"


class ReviewResult(BaseModel):
    """Reviewer output (stub until review agent is wired)."""

    approved: bool = False
    comments: list[str] = Field(default_factory=list)


class AgentState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes."""

    run_id: str
    repo: str
    issue_number: int
    artifact_dir: str
    repo_path: str
    scope_hints: list[str]
    issue_context: dict[str, Any]
    context_bundle: dict[str, Any]
    branch_meta: dict[str, Any]
    fix_plan: dict[str, Any]
    status: AgentStatus
    iteration: int
    last_node: str
    test_result: dict[str, Any]
    review: dict[str, Any]
    patch_applied: bool
    changes_patch_path: str
    commit_sha: str
    commit_message: str
    stop_after_integrate: bool
    error: str | None
