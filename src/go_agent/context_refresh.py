"""Refresh context bundle after test/lint failures during fix iterations."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.code_graph import CodeGraph, build_code_graph, structural_summary
from go_agent.config import Settings
from go_agent.context_builder import (
    ContextBundle,
    ContextFileEntry,
    ContentTier,
    pack_context,
    read_file_lines,
)
from go_agent.context_ranker import RankedFile, rank_files
from go_agent.failure_parse import (
    parse_failing_tests,
    parse_referenced_go_files,
    resolve_test_files,
)
from go_agent.repo_search import RipgrepNotFoundError, RipgrepError, SearchHit, search_repo
from go_agent.run_context import RunContext
from go_agent.utils import normalize_file_path

_TIER_DOWNGRADE: list[ContentTier] = ["full", "summary", "snippet", "structural"]
_MAX_FAILURE_QUERIES = 10


class ContextBundleRefresh(BaseModel):
    iteration: int
    failure_source: Literal["test", "lint", "review"]
    added_paths: list[str] = Field(default_factory=list)
    removed_paths: list[str] = Field(default_factory=list)
    force_full_paths: list[str] = Field(default_factory=list)
    total_chars_before: int
    total_chars_after: int
    budget_chars: int


def _discover_priority_paths(
    repo_path: Path,
    failure_output: str,
    lint_findings: list[dict],
    settings: Settings,
    logger: logging.Logger,
) -> tuple[list[str], list[str], list[str]]:
    """Return (force_full_paths, priority_paths, failing_tests)."""
    failing_tests = parse_failing_tests(failure_output)
    resolved_test_files = resolve_test_files(
        repo_path,
        failing_tests,
        settings,
        logger=logger,
    )
    force_full_paths = [
        path
        for path in resolved_test_files
        if path.endswith("_test.go") and (repo_path / path).is_file()
    ]

    priority: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        norm = normalize_file_path(path)
        if norm in seen:
            return
        if not (repo_path / norm).is_file():
            return
        seen.add(norm)
        priority.append(norm)

    for path in force_full_paths:
        add(path)
    for path in parse_referenced_go_files(failure_output):
        add(path)
    for item in lint_findings:
        raw = item.get("file")
        if raw:
            add(str(raw))

    return force_full_paths, priority, failing_tests


def _failure_search_queries(
    failing_tests: list[str],
    failure_output: str,
    scope_hints: list[str],
) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()

    def add(query: str) -> None:
        cleaned = query.strip()
        if len(cleaned) < 3 or cleaned.lower() in seen:
            return
        seen.add(cleaned.lower())
        queries.append(cleaned)

    for test_name in failing_tests:
        add(test_name)
        add(f"func {test_name}")
    for line in failure_output.splitlines():
        if "FAIL" in line.upper():
            for token in line.split():
                if token.endswith(".go") or "." in token and "_" in token:
                    add(token.strip("`'\"(),"))
    for hint in scope_hints[:_MAX_FAILURE_QUERIES]:
        add(hint)
    return queries[:_MAX_FAILURE_QUERIES]


def _collect_failure_hits(
    repo_path: Path,
    queries: list[str],
    settings: Settings,
    logger: logging.Logger,
) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[tuple[str, int, str]] = set()

    for query in queries:
        try:
            response = search_repo(repo_path, query, settings)
        except RipgrepNotFoundError as exc:
            logger.warning("%s", exc)
            break
        except RipgrepError as exc:
            logger.warning("Context refresh search failed for %r: %s", query, exc)
            continue
        for hit in response.hits:
            key = (hit.path, hit.line_number, hit.query)
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)

    return hits


def _boost_ranked(
    ranked: list[RankedFile],
    force_full_paths: list[str],
    priority_paths: list[str],
) -> list[RankedFile]:
    boosted: list[RankedFile] = []
    seen: set[str] = set()

    def prepend(path: str, rationale: str, score: float) -> None:
        norm = normalize_file_path(path)
        if norm in seen:
            return
        seen.add(norm)
        boosted.append(
            RankedFile(
                path=norm,
                score=score,
                graph_distance=0,
                rationale=rationale,
            )
        )

    for path in force_full_paths:
        prepend(path, "failing test file", 120.0)
    for path in priority_paths:
        if path not in force_full_paths:
            prepend(path, "failure reference", 115.0)
    for item in ranked:
        norm = normalize_file_path(item.path)
        if norm in seen:
            continue
        seen.add(norm)
        boosted.append(item)

    return boosted


def _make_full_entry(
    repo_path: Path,
    path: str,
    *,
    rationale: str = "failing test file",
) -> ContextFileEntry:
    content = "\n".join(read_file_lines(repo_path, path))
    return ContextFileEntry(
        path=path,
        rationale=rationale,
        graph_distance=0,
        content_tier="full",
        content=content,
        char_count=len(content),
    )


def _downgrade_entry(
    entry: ContextFileEntry,
    repo_path: Path,
    graph: CodeGraph,
    hits: list[SearchHit],
    settings: Settings,
) -> ContextFileEntry | None:
    hit = next((item for item in hits if item.path == entry.path), None)
    start_idx = _TIER_DOWNGRADE.index(entry.content_tier) + 1
    for tier in _TIER_DOWNGRADE[start_idx:]:
        if tier == "structural":
            content = structural_summary(graph, entry.path)
        elif tier == "full":
            content = "\n".join(read_file_lines(repo_path, entry.path))
        elif tier == "snippet":
            lines = read_file_lines(repo_path, entry.path)
            if hit is not None:
                radius = settings.context_snippet_radius
                start = max(0, hit.line_number - radius - 1)
                end = min(len(lines), hit.line_number + radius)
                content = "\n".join(lines[start:end])
            else:
                content = "\n".join(lines[:40])
        else:
            continue
        if content:
            return ContextFileEntry(
                path=entry.path,
                rationale=entry.rationale,
                graph_distance=entry.graph_distance,
                content_tier=tier,
                content=content,
                char_count=len(content),
            )
    return None


def _enforce_merged_budget(
    entries: list[ContextFileEntry],
    force_full_norm: set[str],
    repo_path: Path,
    graph: CodeGraph,
    hits: list[SearchHit],
    settings: Settings,
    logger: logging.Logger,
) -> list[ContextFileEntry]:
    budget = settings.context_max_chars
    max_files = settings.context_max_files
    result = list(entries)

    while len(result) > max_files:
        removed = False
        for index in range(len(result) - 1, -1, -1):
            if normalize_file_path(result[index].path) not in force_full_norm:
                result.pop(index)
                removed = True
                break
        if not removed:
            break

    while sum(item.char_count for item in result) > budget:
        total = sum(item.char_count for item in result)
        if total <= budget:
            break

        changed = False
        for index in range(len(result) - 1, -1, -1):
            path_norm = normalize_file_path(result[index].path)
            if path_norm in force_full_norm:
                if result[index].char_count > budget:
                    logger.warning(
                        "Force-full file %s exceeds context budget (%d > %d)",
                        result[index].path,
                        result[index].char_count,
                        budget,
                    )
                    truncated = result[index].content[:budget]
                    result[index] = result[index].model_copy(
                        update={"content": truncated, "char_count": len(truncated)},
                    )
                    changed = True
                continue

            downgraded = _downgrade_entry(
                result[index],
                repo_path,
                graph,
                hits,
                settings,
            )
            if downgraded is not None and downgraded.char_count < result[index].char_count:
                result[index] = downgraded
                changed = True
                break

            result.pop(index)
            changed = True
            break

        if not changed:
            break

    return result


def _merge_entries(
    existing_bundle: ContextBundle,
    fresh_entries: list[ContextFileEntry],
    force_full_paths: list[str],
    priority_paths: list[str],
    repo_path: Path,
) -> list[ContextFileEntry]:
    merged: dict[str, ContextFileEntry] = {}
    order: list[str] = []

    def insert(entry: ContextFileEntry) -> None:
        norm = normalize_file_path(entry.path)
        merged[norm] = entry
        if norm not in order:
            order.append(norm)

    for path in force_full_paths:
        insert(_make_full_entry(repo_path, path))
    for entry in fresh_entries:
        insert(entry)
    for entry in existing_bundle.files:
        norm = normalize_file_path(entry.path)
        if norm not in merged:
            insert(entry)

    priority_norm = {normalize_file_path(path) for path in force_full_paths + priority_paths}
    prioritized = [norm for norm in order if norm in priority_norm]
    remainder = [norm for norm in order if norm not in priority_norm]
    return [merged[norm] for norm in prioritized + remainder]


def refresh_context_for_failure(
    *,
    repo_path: Path,
    existing_bundle: ContextBundle,
    failure_output: str,
    lint_findings: list[dict],
    scope_hints: list[str],
    settings: Settings,
    graph: CodeGraph,
    iteration: int,
    failure_source: Literal["test", "lint", "review"],
    logger: logging.Logger | None = None,
) -> tuple[ContextBundle, ContextBundleRefresh]:
    """Rebuild context bundle prioritizing files discovered from failure output."""
    log = logger or logging.getLogger("go_agent")
    chars_before = existing_bundle.total_chars
    paths_before = {normalize_file_path(entry.path) for entry in existing_bundle.files}

    if not failure_output.strip() and not lint_findings:
        record = ContextBundleRefresh(
            iteration=iteration,
            failure_source=failure_source,
            added_paths=[],
            removed_paths=[],
            force_full_paths=[],
            total_chars_before=chars_before,
            total_chars_after=chars_before,
            budget_chars=settings.context_max_chars,
        )
        return existing_bundle, record

    force_full_paths, priority_paths, failing_tests = _discover_priority_paths(
        repo_path,
        failure_output,
        lint_findings,
        settings,
        log,
    )
    queries = _failure_search_queries(failing_tests, failure_output, scope_hints)
    hits = _collect_failure_hits(repo_path, queries, settings, log)

    ranked = rank_files(graph, hits, settings)
    boosted = _boost_ranked(ranked, force_full_paths, priority_paths)
    fresh_entries = pack_context(repo_path, boosted, hits, graph, settings)

    for path in force_full_paths:
        norm = normalize_file_path(path)
        for index, entry in enumerate(fresh_entries):
            if normalize_file_path(entry.path) == norm:
                fresh_entries[index] = _make_full_entry(repo_path, path)
                break
        else:
            fresh_entries.insert(0, _make_full_entry(repo_path, path))

    merged = _merge_entries(
        existing_bundle,
        fresh_entries,
        force_full_paths,
        priority_paths,
        repo_path,
    )
    force_full_norm = {normalize_file_path(path) for path in force_full_paths}
    final_entries = _enforce_merged_budget(
        merged,
        force_full_norm,
        repo_path,
        graph,
        hits,
        settings,
        log,
    )

    paths_after = {normalize_file_path(entry.path) for entry in final_entries}
    refreshed = ContextBundle(
        issue_number=existing_bundle.issue_number,
        repo=existing_bundle.repo,
        budget_chars=settings.context_max_chars,
        total_chars=sum(entry.char_count for entry in final_entries),
        files=final_entries,
    )
    record = ContextBundleRefresh(
        iteration=iteration,
        failure_source=failure_source,
        added_paths=sorted(paths_after - paths_before),
        removed_paths=sorted(paths_before - paths_after),
        force_full_paths=force_full_paths,
        total_chars_before=chars_before,
        total_chars_after=refreshed.total_chars,
        budget_chars=settings.context_max_chars,
    )
    return refreshed, record


def refresh_context_with_graph_fallback(
    *,
    repo_path: Path,
    existing_bundle: ContextBundle,
    failure_output: str,
    lint_findings: list[dict],
    scope_hints: list[str],
    settings: Settings,
    graph: CodeGraph | None,
    iteration: int,
    failure_source: Literal["test", "lint", "review"],
    logger: logging.Logger | None = None,
) -> tuple[ContextBundle, ContextBundleRefresh]:
    """Refresh context, rebuilding the code graph when no artifact graph is available."""
    log = logger or logging.getLogger("go_agent")
    if graph is None:
        failing_tests = parse_failing_tests(failure_output)
        queries = _failure_search_queries(failing_tests, failure_output, scope_hints)
        hits = _collect_failure_hits(repo_path, queries, settings, log)
        graph = build_code_graph(
            repo_path,
            existing_bundle.repo,
            scope_hints,
            hits,
            settings,
        )
    return refresh_context_for_failure(
        repo_path=repo_path,
        existing_bundle=existing_bundle,
        failure_output=failure_output,
        lint_findings=lint_findings,
        scope_hints=scope_hints,
        settings=settings,
        graph=graph,
        iteration=iteration,
        failure_source=failure_source,
        logger=log,
    )


def write_context_bundle_refresh(ctx: RunContext, record: ContextBundleRefresh) -> Path:
    path = ctx.artifact_dir / "context_bundle_refresh.json"
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "ContextBundleRefresh",
    "refresh_context_for_failure",
    "refresh_context_with_graph_fallback",
    "write_context_bundle_refresh",
]
