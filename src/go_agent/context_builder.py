"""Context builder — scope hints and ripgrep search for downstream agents."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.issue_scope import build_scope_hints
from go_agent.repo_search import (
    RipgrepError,
    RipgrepNotFoundError,
    SearchHit,
    search_scope_hints,
)
from go_agent.run_context import RunContext


class ScopeBundle(BaseModel):
    scope_hints: list[str]
    issue_number: int
    repo: str
    files: list[str] = Field(default_factory=list)


class SearchArtifact(BaseModel):
    issue_number: int
    repo: str
    hits: list[SearchHit]
    files: list[str]


def prepare_scope(issue: IssueContext, settings: Settings) -> ScopeBundle:
    """Build scope hints from issue text for downstream repo search."""
    hints = build_scope_hints(issue, settings)
    return ScopeBundle(
        scope_hints=hints,
        issue_number=issue.number,
        repo=issue.repo,
    )


def enrich_scope_from_search(
    repo_path: Path,
    bundle: ScopeBundle,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> tuple[ScopeBundle, list[SearchHit]]:
    """Search scope hints in the repo and attach matching file paths."""
    log = logger or logging.getLogger("go_agent")
    try:
        hits = search_scope_hints(repo_path, bundle.scope_hints, settings)
    except RipgrepNotFoundError as exc:
        log.warning("%s", exc)
        return bundle, []
    except RipgrepError as exc:
        log.warning("Scope search failed: %s", exc)
        return bundle, []

    files = sorted({hit.path for hit in hits})
    updated = bundle.model_copy(update={"files": files})
    return updated, hits


def build_scope_with_search(
    issue: IssueContext,
    repo_path: Path,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> tuple[ScopeBundle, list[SearchHit]]:
    """Build scope hints and enrich with ripgrep search hits."""
    bundle = prepare_scope(issue, settings)
    return enrich_scope_from_search(repo_path, bundle, settings, logger=logger)


def write_scope_hints(ctx: RunContext, bundle: ScopeBundle) -> Path:
    path = ctx.artifact_dir / "scope_hints.json"
    path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def write_search_hits(
    ctx: RunContext,
    bundle: ScopeBundle,
    hits: list[SearchHit],
) -> Path:
    artifact = SearchArtifact(
        issue_number=bundle.issue_number,
        repo=bundle.repo,
        hits=hits,
        files=bundle.files,
    )
    path = ctx.artifact_dir / "search_hits.json"
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
