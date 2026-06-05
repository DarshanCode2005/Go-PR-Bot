"""Context builder — scope hints, search, graph ranking, and context bundle."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.code_graph import CodeGraph, build_code_graph, structural_summary, write_code_graph
from go_agent.config import Settings
from go_agent.context_ranker import RankedFile, rank_files
from go_agent.github_issues import IssueContext
from go_agent.issue_scope import build_scope_hints
from go_agent.repo_search import (
    RipgrepError,
    RipgrepNotFoundError,
    SearchHit,
    search_scope_hints,
)
from go_agent.run_context import RunContext

ContentTier = Literal["structural", "snippet", "summary", "full"]
_TIER_DOWNGRADE: list[ContentTier] = ["full", "summary", "snippet", "structural"]


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


class ContextFileEntry(BaseModel):
    path: str
    rationale: str
    graph_distance: int
    content_tier: ContentTier
    content: str
    char_count: int


class ContextBundle(BaseModel):
    issue_number: int
    repo: str
    budget_chars: int
    total_chars: int
    files: list[ContextFileEntry]


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


def _best_hit_for_path(path: str, hits: list[SearchHit]) -> SearchHit | None:
    path_hits = [hit for hit in hits if hit.path == path]
    if not path_hits:
        return None
    return path_hits[0]


def _read_file_lines(repo_path: Path, path: str) -> list[str]:
    try:
        return (repo_path / path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _snippet_content(
    repo_path: Path,
    path: str,
    hit: SearchHit | None,
    radius: int,
) -> str:
    lines = _read_file_lines(repo_path, path)
    if not lines:
        return ""
    if hit is None:
        return "\n".join(lines[:40])
    start = max(0, hit.line_number - radius - 1)
    end = min(len(lines), hit.line_number + radius)
    return "\n".join(lines[start:end])


def _summarize_file(path: str, content: str, settings: Settings) -> str | None:
    if not settings.openai_api_key and not settings.anthropic_api_key:
        return None
    try:
        import litellm
    except ImportError:
        return None

    prompt = (
        "Summarize this Go source file in one paragraph for a coding agent fixing a GitHub issue. "
        "Focus on exports, key types, and behavior.\n\n"
        f"File: {path}\n\n{content[:8000]}"
    )
    try:
        response = litellm.completion(
            model=settings.model_fast,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        summary = (response.choices[0].message.content or "").strip()
        return summary or None
    except Exception:
        return None


def _load_tier_content(
    tier: ContentTier,
    repo_path: Path,
    path: str,
    hit: SearchHit | None,
    graph: CodeGraph,
    settings: Settings,
) -> tuple[ContentTier, str]:
    if tier == "structural":
        return "structural", structural_summary(graph, path)
    if tier == "full":
        lines = _read_file_lines(repo_path, path)
        return "full", "\n".join(lines)
    if tier == "summary":
        snippet = _snippet_content(repo_path, path, hit, settings.context_snippet_radius)
        source = snippet or "\n".join(_read_file_lines(repo_path, path)[:120])
        summary = _summarize_file(path, source, settings)
        if summary:
            return "summary", summary
        return "snippet", _snippet_content(repo_path, path, hit, settings.context_snippet_radius)
    return "snippet", _snippet_content(repo_path, path, hit, settings.context_snippet_radius)


def _initial_tier(index: int, settings: Settings) -> ContentTier:
    if index < settings.context_full_file_top_k:
        return "full"
    if index < settings.context_full_file_top_k + settings.context_summary_top_k:
        if settings.openai_api_key or settings.anthropic_api_key:
            return "summary"
        return "snippet"
    return "snippet"


def pack_context(
    repo_path: Path,
    ranked: list[RankedFile],
    hits: list[SearchHit],
    graph: CodeGraph,
    settings: Settings,
) -> list[ContextFileEntry]:
    """Pack ranked files into tiered context entries under the char budget."""
    entries: list[ContextFileEntry] = []
    used_chars = 0
    budget = settings.context_max_chars

    for index, ranked_file in enumerate(ranked):
        remaining = budget - used_chars
        if remaining <= 0:
            break

        hit = _best_hit_for_path(ranked_file.path, hits)
        start_tier = _initial_tier(index, settings)
        start_idx = _TIER_DOWNGRADE.index(start_tier)

        chosen_tier = start_tier
        content = ""
        for tier in _TIER_DOWNGRADE[start_idx:]:
            actual_tier, candidate = _load_tier_content(
                tier,
                repo_path,
                ranked_file.path,
                hit,
                graph,
                settings,
            )
            if len(candidate) <= remaining or actual_tier == "structural":
                chosen_tier = actual_tier
                content = candidate[:remaining] if actual_tier == "structural" and len(candidate) > remaining else candidate
                break

        if not content:
            continue

        entry = ContextFileEntry(
            path=ranked_file.path,
            rationale=ranked_file.rationale,
            graph_distance=ranked_file.graph_distance,
            content_tier=chosen_tier,
            content=content,
            char_count=len(content),
        )
        entries.append(entry)
        used_chars += entry.char_count

    return entries


def build_context_bundle(
    repo_path: Path,
    issue: IssueContext,
    scope_bundle: ScopeBundle,
    search_hits: list[SearchHit],
    settings: Settings,
) -> tuple[CodeGraph, ContextBundle]:
    """Build code graph, rank files, and pack a budget-aware context bundle."""
    graph = build_code_graph(
        repo_path,
        issue.repo,
        scope_bundle.scope_hints,
        search_hits,
        settings,
    )
    ranked = rank_files(graph, search_hits, settings)
    entries = pack_context(repo_path, ranked, search_hits, graph, settings)
    scope_bundle.files = [entry.path for entry in entries]
    bundle = ContextBundle(
        issue_number=issue.number,
        repo=issue.repo,
        budget_chars=settings.context_max_chars,
        total_chars=sum(entry.char_count for entry in entries),
        files=entries,
    )
    return graph, bundle


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


def write_context_bundle(ctx: RunContext, bundle: ContextBundle) -> Path:
    path = ctx.artifact_dir / "context_bundle.json"
    path.write_text(bundle.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "build_context_bundle",
    "build_scope_with_search",
    "ContextBundle",
    "ContextFileEntry",
    "prepare_scope",
    "write_code_graph",
    "write_context_bundle",
    "write_scope_hints",
    "write_search_hits",
]
