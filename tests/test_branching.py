import json
import re

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.constants import APPROVED_REPOS
from go_agent.git_util import run_git
from go_agent.logging_config import configure_run_logging
from go_agent.branching import create_issue_branch, write_branch_meta
from go_agent.run_context import create_run_context
from go_agent.workspace import ensure_repo_cloned

TEST_REPO = APPROVED_REPOS[0]
_SHA = re.compile(r"^[0-9a-f]{40}$")


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_create_issue_branch(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    repo_path = ensure_repo_cloned(
        TEST_REPO,
        ctx,
        configure_run_logging(ctx),
        repo_url=bare_repo_url,
    )

    branch = create_issue_branch(
        repo_path,
        42,
        "Fix: Something Important!",
        configure_run_logging(ctx),
    )

    assert branch.branch_name == "agent/issue-42-fix-something-important"
    assert _SHA.match(branch.base_sha)
    current = run_git(["branch", "--show-current"], cwd=repo_path)
    assert current == branch.branch_name

    meta_path = write_branch_meta(ctx, branch)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["issue_number"] == 42
    assert meta["base_sha"] == branch.base_sha


def test_create_issue_branch_reuses_existing(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    repo_path = ensure_repo_cloned(
        TEST_REPO,
        ctx,
        configure_run_logging(ctx),
        repo_url=bare_repo_url,
    )
    logger = configure_run_logging(ctx)
    first = create_issue_branch(repo_path, 1, "My Issue", logger)
    run_git(["checkout", first.default_branch], cwd=repo_path)
    second = create_issue_branch(repo_path, 1, "My Issue", logger)
    assert second.branch_name == first.branch_name
