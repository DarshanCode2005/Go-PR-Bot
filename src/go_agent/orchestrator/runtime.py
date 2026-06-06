"""Helpers to reconstruct run context and models from LangGraph AgentState."""

from __future__ import annotations

import logging
from pathlib import Path

from go_agent.coder import CoderArtifact
from go_agent.config import get_settings
from go_agent.context_builder import ContextBundle
from go_agent.github_issues import IssueContext
from go_agent.orchestrator.state import AgentState
from go_agent.planner import FixPlan
from go_agent.run_context import RunContext

_LOGGER_NAME = "go_agent"


def run_context_from_state(state: AgentState) -> RunContext:
    """Build RunContext from graph state fields."""
    settings = get_settings()
    artifact_dir = Path(state["artifact_dir"])
    run_id = state["run_id"]
    workspace_dir = settings.work_dir / run_id
    return RunContext(
        run_id=run_id,
        settings=settings,
        artifact_dir=artifact_dir,
        log_path=artifact_dir / "run.log",
        workspace_dir=workspace_dir,
    )


def repo_path_from_state(state: AgentState) -> Path:
    return Path(state["repo_path"])


def issue_from_state(state: AgentState) -> IssueContext:
    data = state.get("issue_context")
    if not data:
        msg = "issue_context missing from state"
        raise ValueError(msg)
    return IssueContext.model_validate(data)


def bundle_from_state(state: AgentState) -> ContextBundle:
    data = state.get("context_bundle")
    if not data:
        msg = "context_bundle missing from state"
        raise ValueError(msg)
    return ContextBundle.model_validate(data)


def plan_from_state(state: AgentState) -> FixPlan:
    data = state.get("fix_plan")
    if not data:
        msg = "fix_plan missing from state"
        raise ValueError(msg)
    return FixPlan.model_validate(data)


def branch_base_sha(state: AgentState) -> str:
    meta = state.get("branch_meta") or {}
    base_sha = meta.get("base_sha")
    if not base_sha:
        msg = "branch_meta.base_sha missing from state"
        raise ValueError(msg)
    return str(base_sha)


def coder_artifact_from_state(state: AgentState) -> CoderArtifact:
    ctx = run_context_from_state(state)
    meta_path = ctx.artifact_dir / "coder_meta.json"
    return CoderArtifact.model_validate_json(meta_path.read_text(encoding="utf-8"))


def logger_for_state(state: AgentState) -> logging.Logger:
    """Return the run-scoped go_agent logger (configured by CLI before invoke)."""
    return logging.getLogger(_LOGGER_NAME)
