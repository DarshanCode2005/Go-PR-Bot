"""Tests for context bundle refresh on fix iterations."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from go_agent.code_graph import CodeGraph
from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.context_refresh import (
    ContextBundleRefresh,
    refresh_context_for_failure,
    write_context_bundle_refresh,
)
from go_agent.repo_search import SearchHit, SearchResponse
from go_agent.run_context import create_run_context

SAMPLE_TEST_OUTPUT = (
    "--- FAIL: TestUnixAddrValidation (0.00s)\n"
    "    validator_test.go:3048: Index: 0 unix_addr failed Error: Key: '' "
    "Error:Field validation for '' failed on the 'unix_addr' tag\n"
    "FAIL\n"
    "FAIL\tgithub.com/go-playground/validator/v10\t0.163s\n"
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _empty_graph() -> CodeGraph:
    return CodeGraph(repo="go-playground/validator", seeds=[])


def _bundle_with_file(path: str, content: str, *, tier: str = "snippet") -> ContextBundle:
    return ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=len(content),
        files=[
            ContextFileEntry(
                path=path,
                rationale="original",
                graph_distance=0,
                content_tier=tier,
                content=content,
                char_count=len(content),
            )
        ],
    )


def _entry_for(bundle: ContextBundle, path: str) -> ContextFileEntry | None:
    for entry in bundle.files:
        if entry.path == path:
            return entry
    return None


def test_injects_resolved_test_file_at_full_tier(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")
    test_body = "package validator\n\nfunc TestUnixAddrValidation(t *testing.T) {}\n"
    (repo_path / "validator_test.go").write_text(test_body, encoding="utf-8")

    existing = _bundle_with_file("baked_in.go", "package validator\n")

    def fake_search(repo_path, query, settings, **kwargs):
        if query == "func TestUnixAddrValidation":
            return SearchResponse(
                query=query,
                glob=kwargs.get("glob"),
                hits=[
                    SearchHit(
                        path="validator_test.go",
                        line_number=3,
                        line_text="func TestUnixAddrValidation(t *testing.T) {}",
                        query=query,
                    )
                ],
                truncated=False,
            )
        return SearchResponse(query=query, glob=None, hits=[], truncated=False)

    with patch("go_agent.failure_parse.search_repo", side_effect=fake_search):
        with patch("go_agent.context_refresh.search_repo", side_effect=fake_search):
            refreshed, record = refresh_context_for_failure(
                repo_path=repo_path,
                existing_bundle=existing,
                failure_output=SAMPLE_TEST_OUTPUT,
                lint_findings=[],
                scope_hints=["unix_addr"],
                settings=Settings(),
                graph=_empty_graph(),
                iteration=1,
                failure_source="test",
            )

    test_entry = _entry_for(refreshed, "validator_test.go")
    assert test_entry is not None
    assert test_entry.content_tier == "full"
    assert "TestUnixAddrValidation" in test_entry.content
    assert "validator_test.go" in record.added_paths
    assert record.force_full_paths == ["validator_test.go"]


def test_merge_preserves_existing_entries(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n", encoding="utf-8")
    (repo_path / "other.go").write_text("package validator\n", encoding="utf-8")
    (repo_path / "validator_test.go").write_text(
        "package validator\n\nfunc TestUnixAddrValidation(t *testing.T) {}\n",
        encoding="utf-8",
    )

    existing = ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=30,
        files=[
            ContextFileEntry(
                path="baked_in.go",
                rationale="original",
                graph_distance=0,
                content_tier="full",
                content="package validator\n",
                char_count=18,
            ),
            ContextFileEntry(
                path="other.go",
                rationale="keep-me",
                graph_distance=1,
                content_tier="snippet",
                content="package validator\n",
                char_count=18,
            ),
        ],
    )

    def fake_search(repo_path, query, settings, **kwargs):
        if query == "func TestUnixAddrValidation":
            return SearchResponse(
                query=query,
                glob=kwargs.get("glob"),
                hits=[
                    SearchHit(
                        path="validator_test.go",
                        line_number=3,
                        line_text="func TestUnixAddrValidation(t *testing.T) {}",
                        query=query,
                    )
                ],
                truncated=False,
            )
        return SearchResponse(query=query, glob=None, hits=[], truncated=False)

    with patch("go_agent.failure_parse.search_repo", side_effect=fake_search):
        with patch("go_agent.context_refresh.search_repo", side_effect=fake_search):
            refreshed, _ = refresh_context_for_failure(
                repo_path=repo_path,
                existing_bundle=existing,
                failure_output=SAMPLE_TEST_OUTPUT,
                lint_findings=[],
                scope_hints=[],
                settings=Settings(),
                graph=_empty_graph(),
                iteration=1,
                failure_source="test",
            )

    paths = {entry.path for entry in refreshed.files}
    assert "other.go" in paths
    assert _entry_for(refreshed, "other.go").rationale == "keep-me"


def test_dedupe_prefers_fresh_content(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("package validator\n// updated\n", encoding="utf-8")
    (repo_path / "validator_test.go").write_text(
        "package validator\n\nfunc TestUnixAddrValidation(t *testing.T) {}\n",
        encoding="utf-8",
    )

    existing = _bundle_with_file("baked_in.go", "package validator\n// stale\n")

    def fake_search(repo_path, query, settings, **kwargs):
        if query == "func TestUnixAddrValidation":
            return SearchResponse(
                query=query,
                glob=kwargs.get("glob"),
                hits=[
                    SearchHit(
                        path="validator_test.go",
                        line_number=3,
                        line_text="func TestUnixAddrValidation(t *testing.T) {}",
                        query=query,
                    ),
                    SearchHit(
                        path="baked_in.go",
                        line_number=1,
                        line_text="package validator",
                        query=query,
                    ),
                ],
                truncated=False,
            )
        if query in {"TestUnixAddrValidation", "func TestUnixAddrValidation", "unix_addr"}:
            return SearchResponse(
                query=query,
                glob=None,
                hits=[
                    SearchHit(
                        path="baked_in.go",
                        line_number=1,
                        line_text="package validator",
                        query=query,
                    )
                ],
                truncated=False,
            )
        return SearchResponse(query=query, glob=None, hits=[], truncated=False)

    with patch("go_agent.failure_parse.search_repo", side_effect=fake_search):
        with patch("go_agent.context_refresh.search_repo", side_effect=fake_search):
            refreshed, _ = refresh_context_for_failure(
                repo_path=repo_path,
                existing_bundle=existing,
                failure_output=SAMPLE_TEST_OUTPUT,
                lint_findings=[],
                scope_hints=["unix_addr"],
                settings=Settings(context_full_file_top_k=3, context_summary_top_k=0),
                graph=_empty_graph(),
                iteration=1,
                failure_source="test",
            )

    baked_entry = _entry_for(refreshed, "baked_in.go")
    assert baked_entry is not None
    assert "// updated" in baked_entry.content
    assert "// stale" not in baked_entry.content


def test_budget_enforcement(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "baked_in.go").write_text("x" * 200, encoding="utf-8")
    (repo_path / "low_priority.go").write_text("y" * 200, encoding="utf-8")
    (repo_path / "validator_test.go").write_text(
        "package validator\n\nfunc TestUnixAddrValidation(t *testing.T) {}\n",
        encoding="utf-8",
    )

    existing = ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=500,
        total_chars=400,
        files=[
            ContextFileEntry(
                path="low_priority.go",
                rationale="drop-me",
                graph_distance=2,
                content_tier="full",
                content="y" * 200,
                char_count=200,
            ),
            ContextFileEntry(
                path="baked_in.go",
                rationale="keep",
                graph_distance=0,
                content_tier="full",
                content="x" * 200,
                char_count=200,
            ),
        ],
    )

    def fake_search(repo_path, query, settings, **kwargs):
        if query == "func TestUnixAddrValidation":
            return SearchResponse(
                query=query,
                glob=kwargs.get("glob"),
                hits=[
                    SearchHit(
                        path="validator_test.go",
                        line_number=3,
                        line_text="func TestUnixAddrValidation(t *testing.T) {}",
                        query=query,
                    )
                ],
                truncated=False,
            )
        return SearchResponse(query=query, glob=None, hits=[], truncated=False)

    settings = Settings(context_max_chars=500, context_max_files=5)

    with patch("go_agent.failure_parse.search_repo", side_effect=fake_search):
        with patch("go_agent.context_refresh.search_repo", side_effect=fake_search):
            refreshed, _ = refresh_context_for_failure(
                repo_path=repo_path,
                existing_bundle=existing,
                failure_output=SAMPLE_TEST_OUTPUT,
                lint_findings=[],
                scope_hints=[],
                settings=settings,
                graph=_empty_graph(),
                iteration=1,
                failure_source="test",
            )

    assert refreshed.total_chars <= settings.context_max_chars
    assert _entry_for(refreshed, "validator_test.go") is not None
    assert _entry_for(refreshed, "validator_test.go").content_tier == "full"


def test_refresh_record_artifact_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    record = ContextBundleRefresh(
        iteration=2,
        failure_source="test",
        added_paths=["validator_test.go"],
        removed_paths=[],
        force_full_paths=["validator_test.go"],
        total_chars_before=100,
        total_chars_after=500,
        budget_chars=80000,
    )
    path = write_context_bundle_refresh(ctx, record)
    assert path == ctx.artifact_dir / "context_bundle_refresh.json"
    assert path.exists()
    payload = path.read_text(encoding="utf-8")
    assert "validator_test.go" in payload
    assert '"iteration": 2' in payload


def test_empty_failure_output_no_crash(tmp_path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    existing = _bundle_with_file("baked_in.go", "package validator\n")

    refreshed, record = refresh_context_for_failure(
        repo_path=repo_path,
        existing_bundle=existing,
        failure_output="",
        lint_findings=[],
        scope_hints=[],
        settings=Settings(),
        graph=_empty_graph(),
        iteration=1,
        failure_source="review",
    )

    assert refreshed.files == existing.files
    assert record.added_paths == []
    assert record.total_chars_before == record.total_chars_after
