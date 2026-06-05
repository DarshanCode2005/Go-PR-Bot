"""Tests for context builder scope stub."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import (
    build_context_bundle,
    build_scope_with_search,
    pack_context,
    prepare_scope,
    write_context_bundle,
    write_scope_hints,
    write_search_hits,
)
from go_agent.code_graph import build_code_graph
from go_agent.context_ranker import rank_files
from go_agent.github_issues import IssueContext
from go_agent.repo_search import SearchHit
from go_agent.run_context import create_run_context

FIXTURES = Path(__file__).parent / "fixtures" / "issue_bodies"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _issue_from_fixture(name: str) -> IssueContext:
    body = (FIXTURES / name).read_text(encoding="utf-8")
    return IssueContext(
        repo="gin-gonic/gin",
        number=42,
        title="Scope test",
        body=body,
        state="open",
    )


def test_prepare_scope_returns_bundle():
    bundle = prepare_scope(_issue_from_fixture("gin_router.md"), Settings())
    assert bundle.issue_number == 42
    assert bundle.repo == "gin-gonic/gin"
    assert bundle.scope_hints
    assert "context.go" in " ".join(bundle.scope_hints)


def test_write_scope_hints_creates_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    bundle = prepare_scope(_issue_from_fixture("cobra_flags.md"), Settings())
    path = write_scope_hints(ctx, bundle)
    assert path == ctx.artifact_dir / "scope_hints.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 42
    assert payload["repo"] == "gin-gonic/gin"
    assert isinstance(payload["scope_hints"], list)
    assert payload["scope_hints"]


def test_build_scope_with_search_populates_files(tmp_path):
    issue = _issue_from_fixture("gin_router.md")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    fake_hits = [
        SearchHit(
            path="context.go",
            line_number=10,
            line_text="func BindJSON()",
            query="BindJSON",
        )
    ]
    with patch(
        "go_agent.context_builder.search_scope_hints",
        return_value=fake_hits,
    ):
        bundle, hits = build_scope_with_search(issue, repo_path, Settings())
    assert hits == fake_hits
    assert bundle.files == ["context.go"]


def test_write_search_hits_creates_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    bundle = prepare_scope(_issue_from_fixture("gin_router.md"), Settings())
    bundle = bundle.model_copy(update={"files": ["context.go"]})
    hits = [
        SearchHit(
            path="context.go",
            line_number=10,
            line_text="func BindJSON()",
            query="BindJSON",
        )
    ]
    path = write_search_hits(ctx, bundle, hits)
    assert path == ctx.artifact_dir / "search_hits.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 42
    assert payload["files"] == ["context.go"]
    assert len(payload["hits"]) == 1


def _bundle_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text("module example.com/foo\n\ngo 1.22\n", encoding="utf-8")
    content = "\n".join(f"line {index}" for index in range(1, 101))
    (repo / "big.go").write_text(content + "\n", encoding="utf-8")
    (repo / "small.go").write_text("package main\n", encoding="utf-8")
    return repo


def test_pack_context_respects_budget(tmp_path):
    repo = _bundle_repo(tmp_path)
    hits = [
        SearchHit(path="big.go", line_number=50, line_text="line 50", query="line"),
        SearchHit(path="small.go", line_number=1, line_text="package main", query="main"),
    ]
    graph = build_code_graph(repo, "owner/repo", [], hits, Settings())
    ranked = rank_files(graph, hits, Settings(context_max_files=2))
    entries = pack_context(
        repo,
        ranked,
        hits,
        graph,
        Settings(context_max_chars=200, context_full_file_top_k=1),
    )
    assert entries
    assert sum(entry.char_count for entry in entries) <= 200
    tiers = {entry.content_tier for entry in entries}
    assert "structural" in tiers or "snippet" in tiers


def test_build_context_bundle_writes_entries_with_rationale(tmp_path):
    issue = _issue_from_fixture("gin_router.md")
    repo = _bundle_repo(tmp_path)
    bundle = prepare_scope(issue, Settings())
    hits = [
        SearchHit(path="small.go", line_number=1, line_text="package main", query="main"),
    ]
    _, context_bundle = build_context_bundle(repo, issue, bundle, hits, Settings())
    assert context_bundle.issue_number == issue.number
    assert context_bundle.files
    assert context_bundle.files[0].rationale


@patch("go_agent.context_builder._summarize_file", return_value="summary text")
def test_build_context_bundle_mock_llm_summary(mock_summary, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    clear_settings_cache()
    issue = _issue_from_fixture("gin_router.md")
    repo = _bundle_repo(tmp_path)
    bundle = prepare_scope(issue, Settings())
    hits = [
        SearchHit(path="small.go", line_number=1, line_text="package main", query="main"),
        SearchHit(path="big.go", line_number=50, line_text="line 50", query="line"),
    ]
    settings = Settings(
        context_full_file_top_k=1,
        context_summary_top_k=2,
    )
    _, context_bundle = build_context_bundle(repo, issue, bundle, hits, settings)
    assert mock_summary.called
    summary_entries = [entry for entry in context_bundle.files if entry.content_tier == "summary"]
    assert summary_entries
    assert summary_entries[0].content == "summary text"


def test_build_context_bundle_includes_rag_search_hit_seeds(tmp_path):
    issue = _issue_from_fixture("gin_router.md")
    repo = _bundle_repo(tmp_path)
    bundle = prepare_scope(issue, Settings())
    hits = [
        SearchHit(
            path="small.go",
            line_number=1,
            line_text="package main",
            query="rag:Fix middleware ordering",
        ),
    ]
    _, context_bundle = build_context_bundle(repo, issue, bundle, hits, Settings())
    paths = {entry.path for entry in context_bundle.files}
    assert "small.go" in paths
    rationales = {entry.rationale for entry in context_bundle.files if entry.path == "small.go"}
    assert "semantic retrieval" in rationales


def test_write_context_bundle_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    issue = _issue_from_fixture("gin_router.md")
    repo = _bundle_repo(tmp_path)
    bundle = prepare_scope(issue, Settings())
    hits = [
        SearchHit(path="small.go", line_number=1, line_text="package main", query="main"),
    ]
    _, context_bundle = build_context_bundle(repo, issue, bundle, hits, Settings())
    ctx = create_run_context()
    path = write_context_bundle(ctx, context_bundle)
    assert path == ctx.artifact_dir / "context_bundle.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["repo"] == issue.repo
    assert payload["budget_chars"] == Settings().context_max_chars
    assert payload["files"]
