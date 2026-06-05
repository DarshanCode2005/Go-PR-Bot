"""Context builder stub — scope hints now; file ranking in a later issue."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.issue_scope import build_scope_hints
from go_agent.run_context import RunContext


class ScopeBundle(BaseModel):
    scope_hints: list[str]
    issue_number: int
    repo: str
    files: list[str] = Field(default_factory=list)


def prepare_scope(issue: IssueContext, settings: Settings) -> ScopeBundle:
    """Build scope hints from issue text for downstream repo search."""
    hints = build_scope_hints(issue, settings)
    return ScopeBundle(
        scope_hints=hints,
        issue_number=issue.number,
        repo=issue.repo,
    )


def write_scope_hints(ctx: RunContext, bundle: ScopeBundle) -> Path:
    path = ctx.artifact_dir / "scope_hints.json"
    path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
