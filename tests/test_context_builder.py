"""Tests for context builder scope stub."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import (
    build_scope_with_search,
    prepare_scope,
    write_scope_hints,
    write_search_hits,
)
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
