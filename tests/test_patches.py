"""Tests for patch apply, commit, and changes.patch export."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.branching import create_issue_branch
from go_agent.cli import app
from go_agent.config import Settings, clear_settings_cache
from go_agent.constants import APPROVED_REPOS
from go_agent.git_util import run_git
from go_agent.logging_config import configure_run_logging
from go_agent.github_issues import IssueContext
from go_agent.patches import (
    PatchApplyError,
    apply_patch_and_commit,
    apply_unified_patch,
    export_changes_patch,
    format_commit_message,
)
from go_agent.run_context import create_run_context
from go_agent.workspace import ensure_repo_cloned
from helpers import enable_planner_mock

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

INVALID_PATCH = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1,3 +1,3 @@
-this line is not in the file
+still wrong
 still wrong line two
 still wrong line three
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


def test_format_commit_message():
    msg = format_commit_message("Fix router bug", 42)
    assert msg == "fix: Fix router bug (fixes #42)"


def test_apply_valid_patch_and_commit(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)

    result = apply_patch_and_commit(
        repo_path,
        ctx,
        README_PATCH,
        42,
        "Update readme",
        branch.base_sha,
        logger,
    )

    assert (repo_path / "README.md").read_text(encoding="utf-8") == "v2\n"
    assert result.changes_patch_path == ctx.artifact_dir / "changes.patch"
    assert result.changes_patch_path.exists()
    assert "-v1" in result.changes_patch_path.read_text(encoding="utf-8")
    assert "+v2" in result.changes_patch_path.read_text(encoding="utf-8")
    assert "fixes #42" in result.commit_message


def test_apply_invalid_patch_raises(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    _ctx, repo_path, _branch, _logger = _setup_repo(tmp_path, bare_repo_url)

    with pytest.raises(PatchApplyError, match="git apply"):
        apply_unified_patch(repo_path, INVALID_PATCH)


def test_export_changes_patch(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    apply_unified_patch(repo_path, README_PATCH)
    run_git(["add", "-A"], cwd=repo_path)
    run_git(["commit", "-m", "test commit"], cwd=repo_path)

    dest = tmp_path / "out.patch"
    export_changes_patch(repo_path, branch.base_sha, dest)
    content = dest.read_text(encoding="utf-8")
    assert "+v2" in content


def test_export_failure_leaves_no_commit(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)
    head_before = run_git(["rev-parse", "HEAD"], cwd=repo_path)

    with patch(
        "go_agent.patches.export_changes_patch",
        side_effect=PatchApplyError("git diff timed out"),
    ):
        with pytest.raises(PatchApplyError, match="git diff timed out"):
            apply_patch_and_commit(
                repo_path,
                ctx,
                README_PATCH,
                42,
                "Update readme",
                branch.base_sha,
                logger,
            )

    assert run_git(["rev-parse", "HEAD"], cwd=repo_path) == head_before
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "v1\n"
    assert not (ctx.artifact_dir / "changes.patch").exists()


def test_recovers_orphaned_commit_when_export_failed(
    tmp_path, monkeypatch, bare_repo_url: str
):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)

    apply_unified_patch(repo_path, README_PATCH)
    run_git(["commit", "-am", "orphaned commit"], cwd=repo_path)
    commit_sha = run_git(["rev-parse", "HEAD"], cwd=repo_path)

    result = apply_patch_and_commit(
        repo_path,
        ctx,
        README_PATCH,
        42,
        "Update readme",
        branch.base_sha,
        logger,
    )

    assert result.commit_sha == commit_sha
    assert result.changes_patch_path.exists()
    assert "+v2" in result.changes_patch_path.read_text(encoding="utf-8")


def test_apply_stack_on_head_commits_second_patch(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    ctx, repo_path, branch, logger = _setup_repo(tmp_path, bare_repo_url)

    first = apply_patch_and_commit(
        repo_path,
        ctx,
        README_PATCH,
        42,
        "Update readme",
        branch.base_sha,
        logger,
    )
    assert first.commit_sha

    second_patch = """\
diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-v2
+v3
"""
    second = apply_patch_and_commit(
        repo_path,
        ctx,
        second_patch,
        42,
        "Update readme again",
        branch.base_sha,
        logger,
        stack_on_head=True,
    )

    assert second.commit_sha != first.commit_sha
    assert (repo_path / "README.md").read_text(encoding="utf-8") == "v3\n"
    assert "+v3" in second.changes_patch_path.read_text(encoding="utf-8")


def test_cli_patch_file(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))
    enable_planner_mock(monkeypatch)

    patch_path = tmp_path / "fix.patch"
    patch_path.write_text(README_PATCH, encoding="utf-8")

    issue_ctx = IssueContext(
        repo="gin-gonic/gin",
        number=42,
        title="Update readme",
        state="open",
    )
    with patch("go_agent.cli.fetch_issue_context", return_value=issue_ctx):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            result = runner.invoke(
                app,
                [
                    "run",
                    "--repo",
                    "gin-gonic/gin",
                    "--issue",
                    "42",
                    "--patch-file",
                    str(patch_path),
                ],
            )

    assert result.exit_code == 0
    artifact_dirs = [p for p in (tmp_path / "artifacts").iterdir() if p.is_dir()]
    changes = artifact_dirs[0] / "changes.patch"
    assert changes.exists()
    assert "+v2" in changes.read_text(encoding="utf-8")
