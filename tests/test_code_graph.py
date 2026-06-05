"""Tests for lightweight code graph builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from go_agent.code_graph import build_code_graph, write_code_graph
from go_agent.config import Settings, clear_settings_cache
from go_agent.repo_search import SearchHit
from go_agent.run_context import create_run_context


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _make_graph_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(
        "module example.com/foo\n\ngo 1.22\n",
        encoding="utf-8",
    )
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "foo.go").write_text(
        'package pkg\n\nimport "example.com/foo/pkg/bar"\n',
        encoding="utf-8",
    )
    (pkg / "foo_test.go").write_text("package pkg\n", encoding="utf-8")
    bar = pkg / "bar"
    bar.mkdir()
    (bar / "bar.go").write_text("package bar\n", encoding="utf-8")

    vendor = repo / "vendor"
    vendor.mkdir()
    (vendor / "skip.go").write_text("package vendor\n", encoding="utf-8")

    gen = repo / "pkg" / "widget.pb.go"
    gen.write_text("package pkg\n", encoding="utf-8")
    return repo


def test_tests_edge_pairs_foo_and_foo_test(tmp_path):
    repo = _make_graph_repo(tmp_path)
    graph = build_code_graph(repo, "owner/repo", [], [], Settings())
    test_edges = [
        edge
        for edge in graph.edges
        if edge.kind == "tests"
        and {"file:pkg/foo.go", "file:pkg/foo_test.go"} == {edge.source, edge.target}
    ]
    assert len(test_edges) == 2


def test_seeds_from_search_hits(tmp_path):
    repo = _make_graph_repo(tmp_path)
    hits = [
        SearchHit(
            path="pkg/foo.go",
            line_number=1,
            line_text="package pkg",
            query="foo",
        )
    ]
    graph = build_code_graph(repo, "owner/repo", [], hits, Settings())
    assert "file:pkg/foo.go" in graph.seeds


def test_skips_vendor_and_pb_go(tmp_path):
    repo = _make_graph_repo(tmp_path)
    graph = build_code_graph(repo, "owner/repo", [], [], Settings())
    labels = {node.label for node in graph.nodes}
    assert "vendor/skip.go" not in labels
    assert "pkg/widget.pb.go" not in labels


def test_write_code_graph_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _make_graph_repo(tmp_path)
    ctx = create_run_context()
    graph = build_code_graph(repo, "owner/repo", ["pkg/foo.go"], [], Settings())
    path = write_code_graph(ctx, graph)
    assert path == ctx.artifact_dir / "code_graph.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["repo"] == "owner/repo"
    assert payload["module_path"] == "example.com/foo"
    assert payload["seeds"]
