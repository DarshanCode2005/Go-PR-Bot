from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.config import clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.llm_client import set_completion_transport
from helpers import enable_planner_mock

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    set_completion_transport(None)
    yield
    set_completion_transport(None)
    clear_settings_cache()


def test_run_writes_branch_meta(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))
    enable_planner_mock(monkeypatch)

    issue_ctx = IssueContext(
        repo="gin-gonic/gin",
        number=99,
        title="Fix Stuff",
        state="open",
    )
    with patch("go_agent.cli.fetch_issue_context", return_value=issue_ctx):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            result = runner.invoke(
                app,
                ["run", "--repo", "gin-gonic/gin", "--issue", "99"],
            )

    assert result.exit_code == 0
    artifact_dirs = [p for p in (tmp_path / "artifacts").iterdir() if p.is_dir()]
    assert len(artifact_dirs) == 1
    branch_meta = artifact_dirs[0] / "branch_meta.json"
    assert branch_meta.exists()
    assert "agent/issue-99-fix-stuff" in branch_meta.read_text(encoding="utf-8")
    assert (artifact_dirs[0] / "plan.json").exists()
    assert (artifact_dirs[0] / "proposed.patch").exists()
    assert (artifact_dirs[0] / "coder_meta.json").exists()
    assert (artifact_dirs[0] / "integrator_meta.json").exists()
    assert (artifact_dirs[0] / "changes.patch").exists()
