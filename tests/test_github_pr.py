"""Tests for gh pr create integration."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.branching import create_issue_branch
from go_agent.cli import app
from go_agent.config import Settings, clear_settings_cache
from go_agent.constants import APPROVED_REPOS
from go_agent.github_issues import IssueContext
from go_agent.github_pr import (
    PRCreateError,
    PRResult,
    count_commits_ahead,
    create_draft_pr,
    maybe_create_pr,
    push_branch,
    write_pr_meta,
)
from go_agent.logging_config import configure_run_logging
from go_agent.patches import apply_patch_and_commit
from go_agent.pr_writer import build_pr_template
from go_agent.run_context import create_run_context
from go_agent.workspace import ensure_repo_cloned

TEST_REPO = APPROVED_REPOS[0]
runner = CliRunner()

README_PATCH = """\
diff --git a/README.md b/README.md
index 0000000..1111111 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-v1
+v2
"""


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _setup_repo(tmp_path, bare_repo_url: str):
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)
    repo_path = ensure_repo_cloned(TEST_REPO, ctx, logger, repo_url=bare_repo_url)
    branch = create_issue_branch(repo_path, 42, "Update readme", logger)
    return ctx, repo_path, branch, logger


def test_count_commits_ahead_zero_at_base(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    _ctx, repo_path, branch, _logger = _setup_repo(tmp_path, bare_repo_url)
    assert count_commits_ahead(repo_path, branch.base_sha) == 0


def test_count_commits_ahead_after_patch(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    apply_patch_and_commit(
        repo_path,
        ctx,
        README_PATCH,
        42,
        "Update readme",
        branch.base_sha,
        logger,
    )
    assert count_commits_ahead(repo_path, branch.base_sha) == 1


def test_push_branch_calls_git(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    _ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    with patch("go_agent.github_pr.run_git") as run_git:
        push_branch(repo_path, branch.branch_name, logger)
    run_git.assert_called_once_with(
        ["push", "-u", "origin", branch.branch_name],
        cwd=repo_path,
    )


def test_create_draft_pr_parses_url(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    _ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    url = "https://github.com/gin-gonic/gin/pull/99"
    with patch("go_agent.github_pr.shutil.which", return_value="/usr/bin/gh"):
        with patch("go_agent.github_pr.subprocess.run") as run:
            run.return_value.stdout = f"{url}\n"
            run.return_value.returncode = 0
            result = create_draft_pr(
                repo_path,
                TEST_REPO,
                branch.default_branch,
                branch.branch_name,
                "fix: Update readme (fixes #42)",
                "## Problem\n\nTest",
                logger,
            )
    assert result.url == url
    args = run.call_args[0][0]
    assert "gh" in args
    assert "pr" in args
    assert "create" in args
    assert "--draft" in args


def test_maybe_create_pr_missing_gh_skips_push(
    tmp_path, monkeypatch, bare_repo_url: str
):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    apply_patch_and_commit(
        repo_path,
        ctx,
        README_PATCH,
        42,
        "Update readme",
        branch.base_sha,
        logger,
    )
    draft = build_pr_template(
        IssueContext(
            repo=TEST_REPO,
            number=42,
            title="Update readme",
            state="open",
        )
    )
    with patch("go_agent.github_pr.shutil.which", return_value=None):
        with patch("go_agent.github_pr.push_branch") as push:
            with pytest.raises(PRCreateError, match="Install and authenticate"):
                maybe_create_pr(repo_path, TEST_REPO, branch, draft, ctx, logger)
    push.assert_not_called()


def test_maybe_create_pr_no_commits_raises(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    draft = build_pr_template(
        IssueContext(
            repo=TEST_REPO,
            number=42,
            title="Update readme",
            state="open",
        )
    )
    with pytest.raises(PRCreateError, match="no commits beyond base"):
        maybe_create_pr(repo_path, TEST_REPO, branch, draft, ctx, logger)


def test_write_pr_meta(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    result = PRResult(
        url="https://github.com/gin-gonic/gin/pull/99",
        title="fix: Example (fixes #42)",
        branch_name="agent/issue-42-update-readme",
    )
    path = write_pr_meta(ctx, result)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["url"] == result.url
    assert payload["branch_name"] == result.branch_name


def test_dry_run_does_not_call_gh(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))

    issue_ctx = IssueContext(
        repo=TEST_REPO,
        number=42,
        title="Update readme",
        state="open",
    )
    with patch("go_agent.cli.fetch_issue_context", return_value=issue_ctx):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            with patch("go_agent.cli.maybe_create_pr") as maybe_create:
                result = runner.invoke(
                    app,
                    ["run", "--repo", TEST_REPO, "--issue", "42", "--dry-run"],
                )
    maybe_create.assert_not_called()
    assert result.exit_code == 1


def test_create_pr_path_calls_gh(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))

    patch_path = tmp_path / "fix.patch"
    patch_path.write_text(README_PATCH, encoding="utf-8")

    issue_ctx = IssueContext(
        repo=TEST_REPO,
        number=42,
        title="Update readme",
        state="open",
    )
    pr_url = "https://github.com/gin-gonic/gin/pull/100"
    with patch("go_agent.cli.fetch_issue_context", return_value=issue_ctx):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            with patch(
                "go_agent.cli.maybe_create_pr",
                return_value=PRResult(
                    url=pr_url,
                    title="fix: Update readme (fixes #42)",
                    branch_name="agent/issue-42-update-readme",
                ),
            ) as maybe_create:
                result = runner.invoke(
                    app,
                    [
                        "run",
                        "--repo",
                        TEST_REPO,
                        "--issue",
                        "42",
                        "--no-dry-run",
                        "--create-pr",
                        "--patch-file",
                        str(patch_path),
                    ],
                )

    maybe_create.assert_called_once()
    assert result.exit_code == 0
    assert pr_url in result.stdout
