"""Tests for optional semantic RAG retrieval."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.repo_rag import (
    RagDepsNotFoundError,
    RagHit,
    build_rag_query,
    chunk_go_files,
    merge_search_hits,
    rag_hits_to_search_hits,
    retrieve_rag_hits,
    write_rag_hits,
)
from go_agent.repo_search import SearchHit
from go_agent.run_context import create_run_context


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _issue() -> IssueContext:
    return IssueContext(
        repo="owner/repo",
        number=7,
        title="Fix middleware ordering",
        body="The middleware chain is broken for nested groups.",
        state="open",
    )


def _make_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/foo\n\ngo 1.22\n", encoding="utf-8")
    lines = "\n".join(f"line {index}" for index in range(1, 121))
    (repo / "handler.go").write_text(lines + "\n", encoding="utf-8")
    return repo


def test_build_rag_query_uses_title_and_body():
    query = build_rag_query(_issue())
    assert "Fix middleware ordering" in query
    assert "middleware chain" in query


def test_chunk_go_files_splits_with_overlap(tmp_path):
    repo = _make_repo(tmp_path)
    settings = Settings(rag_chunk_lines=40, rag_chunk_overlap=10)
    chunks = chunk_go_files(repo, settings)
    assert chunks
    assert chunks[0].path == "handler.go"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 40
    assert chunks[1].start_line == 31


def test_rag_hits_to_search_hits_adapter():
    hits = [
        RagHit(
            path="handler.go",
            line_number=10,
            line_text="line 10",
            query="Fix middleware ordering",
            score=0.9,
            chunk_start=1,
            chunk_end=40,
        )
    ]
    adapted = rag_hits_to_search_hits(hits)
    assert len(adapted) == 1
    assert adapted[0].path == "handler.go"
    assert adapted[0].query.startswith("rag:")


def test_merge_search_hits_dedupes():
    primary = [
        SearchHit(path="a.go", line_number=1, line_text="rg", query="BindJSON"),
    ]
    secondary = [
        SearchHit(path="a.go", line_number=1, line_text="rag", query="rag:semantic"),
        SearchHit(path="b.go", line_number=5, line_text="rag", query="rag:semantic"),
    ]
    merged = merge_search_hits(primary, secondary)
    by_key = {(hit.path, hit.line_number): hit for hit in merged}
    assert by_key[("a.go", 1)].query == "BindJSON"
    assert ("b.go", 5) in by_key


def test_retrieve_rag_hits_disabled_returns_empty(tmp_path):
    repo = _make_repo(tmp_path)
    hits = retrieve_rag_hits(repo, _issue(), "owner/repo", Settings(enable_rag=False))
    assert hits == []


def test_retrieve_rag_hits_missing_deps_fallback(tmp_path):
    repo = _make_repo(tmp_path)
    settings = Settings(enable_rag=True)
    with patch(
        "go_agent.repo_rag.get_or_build_index",
        side_effect=RagDepsNotFoundError("missing chromadb"),
    ):
        hits = retrieve_rag_hits(repo, _issue(), "owner/repo", settings)
    assert hits == []


def test_retrieve_rag_hits_returns_mocked_hits(tmp_path):
    repo = _make_repo(tmp_path)
    settings = Settings(enable_rag=True)
    mocked_hits = [
        RagHit(
            path="handler.go",
            line_number=1,
            line_text="line 1",
            query="Fix middleware ordering",
            score=0.88,
            chunk_start=1,
            chunk_end=40,
        )
    ]
    with (
        patch("go_agent.repo_rag._resolve_repo_head", return_value="a" * 40),
        patch("go_agent.repo_rag.get_or_build_index", return_value=MagicMock(count=lambda: 1)),
        patch("go_agent.repo_rag.retrieve_chunks", return_value=mocked_hits),
    ):
        hits = retrieve_rag_hits(repo, _issue(), "owner/repo", settings)
    assert hits == mocked_hits


def test_write_rag_hits_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    hits = [
        RagHit(
            path="handler.go",
            line_number=1,
            line_text="line 1",
            query="query",
            score=0.5,
            chunk_start=1,
            chunk_end=40,
        )
    ]
    path = write_rag_hits(ctx, _issue(), "query", hits)
    assert path == ctx.artifact_dir / "rag_hits.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 7
    assert payload["repo"] == "owner/repo"
    assert len(payload["hits"]) == 1
