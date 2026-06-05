"""Tests for weighted BFS file ranking."""

from __future__ import annotations

from pathlib import Path

import pytest

from go_agent.code_graph import build_code_graph
from go_agent.config import Settings, clear_settings_cache
from go_agent.context_ranker import rank_files
from go_agent.repo_search import SearchHit


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _make_rank_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/foo\n\ngo 1.22\n", encoding="utf-8")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "foo.go").write_text('package pkg\nimport "fmt"\n', encoding="utf-8")
    (pkg / "foo_test.go").write_text("package pkg\n", encoding="utf-8")
    (pkg / "bar.go").write_text("package pkg\n", encoding="utf-8")
    return repo


def test_bfs_distance_from_seed(tmp_path):
    repo = _make_rank_repo(tmp_path)
    hits = [
        SearchHit(path="pkg/foo.go", line_number=1, line_text="x", query="foo"),
    ]
    graph = build_code_graph(repo, "owner/repo", [], hits, Settings())
    ranked = rank_files(graph, hits, Settings(context_graph_max_hops=2))
    by_path = {item.path: item for item in ranked}
    assert by_path["pkg/foo.go"].graph_distance == 0
    assert by_path["pkg/bar.go"].graph_distance == 1


def test_test_file_included_when_source_ranked(tmp_path):
    repo = _make_rank_repo(tmp_path)
    hits = [
        SearchHit(path="pkg/foo.go", line_number=1, line_text="x", query="foo"),
    ]
    graph = build_code_graph(repo, "owner/repo", [], hits, Settings())
    ranked = rank_files(graph, hits, Settings())
    paths = [item.path for item in ranked]
    assert "pkg/foo.go" in paths
    assert "pkg/foo_test.go" in paths


def test_max_files_cap(tmp_path):
    repo = _make_rank_repo(tmp_path)
    hits = [
        SearchHit(path="pkg/foo.go", line_number=1, line_text="x", query="foo"),
    ]
    graph = build_code_graph(repo, "owner/repo", [], hits, Settings())
    ranked = rank_files(graph, hits, Settings(context_max_files=2))
    assert len(ranked) <= 2
