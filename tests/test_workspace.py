"""Tests for workspace clone and cache."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.config import Settings, clear_settings_cache
from go_agent.constants import APPROVED_REPOS
from go_agent.logging_config import configure_run_logging
from go_agent.run_context import create_run_context
from go_agent.workspace import (
    CloneError,
    RepoNotAllowedError,
    assert_repo_allowed,
    ensure_repo_cloned,
    resolve_remote_head,
)

from helpers import bump_bare_repo

runner = CliRunner()
TEST_REPO = APPROVED_REPOS[0]


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_assert_repo_allowed_rejects():
    with pytest.raises(RepoNotAllowedError, match="not allowed"):
        assert_repo_allowed("evil/repo")


def test_shallow_clone_to_run_dir(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)

    dest = ensure_repo_cloned(TEST_REPO, ctx, logger, repo_url=bare_repo_url)

    assert dest == ctx.workspace_dir / "repo"
    assert (dest / ".git").exists()
    assert (dest / "README.md").read_text(encoding="utf-8") == "v1\n"

    meta = json.loads((ctx.artifact_dir / "repo_meta.json").read_text(encoding="utf-8"))
    assert meta["repo"] == TEST_REPO
    assert meta["cache_hit"] is False
    assert meta["remote_head"] == resolve_remote_head(bare_repo_url)


def test_cache_hit_skips_shallow_clone(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )

    ctx1 = create_run_context(settings)
    ensure_repo_cloned(TEST_REPO, ctx1, configure_run_logging(ctx1), repo_url=bare_repo_url)

    ctx2 = create_run_context(settings)
    with patch("go_agent.workspace._update_cache") as update_cache:
        dest2 = ensure_repo_cloned(
            TEST_REPO,
            ctx2,
            configure_run_logging(ctx2),
            repo_url=bare_repo_url,
        )
        update_cache.assert_not_called()

    meta = json.loads((ctx2.artifact_dir / "repo_meta.json").read_text(encoding="utf-8"))
    assert meta["cache_hit"] is True
    assert (dest2 / "README.md").read_text(encoding="utf-8") == "v1\n"


def test_cache_invalidates_on_new_head(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )

    ctx1 = create_run_context(settings)
    ensure_repo_cloned(TEST_REPO, ctx1, configure_run_logging(ctx1), repo_url=bare_repo_url)

    bump_bare_repo(bare_repo_url, tmp_path)
    ctx2 = create_run_context(settings)
    dest2 = ensure_repo_cloned(
        TEST_REPO,
        ctx2,
        configure_run_logging(ctx2),
        repo_url=bare_repo_url,
    )

    meta = json.loads((ctx2.artifact_dir / "repo_meta.json").read_text(encoding="utf-8"))
    assert meta["cache_hit"] is False
    assert (dest2 / "README.md").read_text(encoding="utf-8") == "v2\n"


def test_ensure_repo_idempotent_within_run(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)
    first = ensure_repo_cloned(TEST_REPO, ctx, logger, repo_url=bare_repo_url)
    second = ensure_repo_cloned(TEST_REPO, ctx, logger, repo_url=bare_repo_url)
    assert first == second


def test_cli_rejects_invalid_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))

    result = runner.invoke(app, ["run", "--repo", "fake/repo", "--issue", "1"])
    assert result.exit_code != 0
    assert "not allowed" in (result.stdout + result.stderr).lower()
    workspaces = tmp_path / "workspaces"
    if workspaces.exists():
        run_dirs = [p for p in workspaces.iterdir() if p.is_dir() and p.name != "_cache"]
        for run_dir in run_dirs:
            assert not (run_dir / "repo").exists()


def test_resolve_remote_head_invalid_url():
    with pytest.raises(CloneError):
        resolve_remote_head("file:///does-not-exist/remote.git")
