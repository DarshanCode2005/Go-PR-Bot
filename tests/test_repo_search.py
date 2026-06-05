"""Tests for ripgrep repository search."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.repo_search import (
    RipgrepError,
    RipgrepNotFoundError,
    SearchHit,
    _parse_rg_line,
    search_repo,
    search_scope_hints,
)


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_parse_rg_line():
    hit = _parse_rg_line("context.go:42:func BindJSON()", "BindJSON")
    assert hit is not None
    assert hit.path == "context.go"
    assert hit.line_number == 42
    assert hit.line_text == "func BindJSON()"
    assert hit.query == "BindJSON"


def test_parse_rg_line_invalid():
    assert _parse_rg_line("not-a-match", "q") is None


def test_search_repo_returns_hits(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("go_agent.repo_search.shutil.which", return_value="/usr/bin/rg"):
        with patch("go_agent.repo_search.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["rg"],
                returncode=0,
                stdout="context.go:10:func BindJSON()\nrouter/router.go:5:BindJSON\n",
                stderr="",
            )
            response = search_repo(repo, "BindJSON", Settings(ripgrep_max_results=50))

    assert len(response.hits) == 2
    assert response.hits[0].path == "context.go"
    assert response.query == "BindJSON"
    assert response.truncated is False
    args = run.call_args[0][0]
    assert "--no-config" in args
    assert "--color" in args
    assert args[args.index("--color") + 1] == "never"
    assert "--fixed-strings" in args
    assert "BindJSON" in args


def test_search_repo_no_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("go_agent.repo_search.shutil.which", return_value="/usr/bin/rg"):
        with patch("go_agent.repo_search.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["rg"],
                returncode=1,
                stdout="",
                stderr="",
            )
            response = search_repo(repo, "MissingSymbol", Settings())

    assert response.hits == []


def test_search_repo_timeout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("go_agent.repo_search.shutil.which", return_value="/usr/bin/rg"):
        with patch(
            "go_agent.repo_search.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["rg"], timeout=1),
        ):
            with pytest.raises(RipgrepError, match="timed out"):
                search_repo(repo, "BindJSON", Settings(ripgrep_timeout=1))


def test_search_repo_not_found(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("go_agent.repo_search.shutil.which", return_value=None):
        with pytest.raises(RipgrepNotFoundError, match="Install ripgrep"):
            search_repo(repo, "BindJSON", Settings())


def test_search_repo_truncated(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with patch("go_agent.repo_search.shutil.which", return_value="/usr/bin/rg"):
        with patch("go_agent.repo_search.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["rg"],
                returncode=0,
                stdout="a.go:1:line\n",
                stderr="",
            )
            response = search_repo(repo, "line", Settings(ripgrep_max_results=1))

    assert response.truncated is True
    assert "--max-total-count" in run.call_args[0][0]


def test_search_repo_caps_total_hits_across_files(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    stdout = "a.go:1:line\nb.go:1:line\nc.go:1:line\n"
    with patch("go_agent.repo_search.shutil.which", return_value="/usr/bin/rg"):
        with patch("go_agent.repo_search.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(
                args=["rg"],
                returncode=0,
                stdout=stdout,
                stderr="",
            )
            response = search_repo(repo, "line", Settings(ripgrep_max_results=2))

    assert len(response.hits) == 2
    assert response.truncated is True


def test_search_scope_hints_dedupes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    hits = [
        SearchHit(path="a.go", line_number=1, line_text="BindJSON", query="BindJSON"),
        SearchHit(path="a.go", line_number=1, line_text="BindJSON", query="bindjson"),
    ]

    def fake_search(_repo, query, _settings, **_kwargs):
        from go_agent.repo_search import SearchResponse

        if query == "BindJSON":
            return SearchResponse(query=query, glob="*.go", hits=[hits[0]])
        return SearchResponse(query=query, glob="*.go", hits=[hits[1]])

    with patch("go_agent.repo_search.search_repo", side_effect=fake_search):
        merged = search_scope_hints(repo, ["BindJSON", "bindjson", "go"], Settings())

    assert len(merged) == 1
