"""Persisted metadata for resuming interrupted runs."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from go_agent.branching import BranchInfo
from go_agent.config import Settings, get_settings
from go_agent.context_builder import ScopeBundle
from go_agent.github_issues import IssueContext
from go_agent.run_context import RunContext


class RunMetaError(RuntimeError):
    """Raised when run metadata is missing or invalid."""


class RunMeta(BaseModel):
    run_id: str
    repo: str
    issue_number: int
    dry_run: bool = True
    create_pr: bool = False
    force: bool = False
    enable_rag: bool = False
    artifact_dir: str
    repo_path: str
    workspace_dir: str
    include_test: bool = True
    include_closed_loop: bool = True
    patch_file: str | None = None


def write_run_meta(ctx: RunContext, meta: RunMeta) -> Path:
    path = ctx.artifact_dir / "run_meta.json"
    path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_run_meta(run_id: str, settings: Settings | None = None) -> RunMeta:
    settings = settings or get_settings()
    path = settings.artifacts_dir / run_id / "run_meta.json"
    if not path.is_file():
        msg = f"run metadata not found for run_id={run_id!r} (expected {path})"
        raise RunMetaError(msg)
    return RunMeta.model_validate_json(path.read_text(encoding="utf-8"))


def resolve_run_context(run_id: str, settings: Settings | None = None) -> RunContext:
    settings = settings or get_settings()
    artifact_dir = settings.artifacts_dir / run_id
    if not artifact_dir.is_dir():
        msg = f"artifact directory not found for run_id={run_id!r} (expected {artifact_dir})"
        raise RunMetaError(msg)
    return RunContext(
        run_id=run_id,
        settings=settings,
        artifact_dir=artifact_dir,
        log_path=artifact_dir / "run.log",
        workspace_dir=settings.work_dir / run_id,
    )


def load_issue_context(ctx: RunContext) -> IssueContext:
    path = ctx.artifact_dir / "issue_context.json"
    if not path.is_file():
        msg = f"issue_context.json missing in {ctx.artifact_dir}"
        raise RunMetaError(msg)
    return IssueContext.model_validate_json(path.read_text(encoding="utf-8"))


def load_branch_info(ctx: RunContext) -> BranchInfo:
    path = ctx.artifact_dir / "branch_meta.json"
    if not path.is_file():
        msg = f"branch_meta.json missing in {ctx.artifact_dir}"
        raise RunMetaError(msg)
    data = json.loads(path.read_text(encoding="utf-8"))
    return BranchInfo(
        branch_name=data["branch_name"],
        base_sha=data["base_sha"],
        default_branch=data["default_branch"],
        issue_number=data["issue_number"],
        issue_title=data.get("issue_title", ""),
    )


def load_scope_bundle(ctx: RunContext) -> ScopeBundle:
    path = ctx.artifact_dir / "scope_hints.json"
    if not path.is_file():
        msg = f"scope_hints.json missing in {ctx.artifact_dir}"
        raise RunMetaError(msg)
    return ScopeBundle.model_validate_json(path.read_text(encoding="utf-8"))
