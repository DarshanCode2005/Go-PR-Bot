from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.config import clear_settings_cache

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_run_writes_branch_meta(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))

    with patch("go_agent.cli.fetch_issue_title", return_value="Fix Stuff"):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            result = runner.invoke(
                app,
                ["run", "--repo", "gin-gonic/gin", "--issue", "99"],
            )

    assert result.exit_code == 1
    artifact_dirs = [p for p in (tmp_path / "artifacts").iterdir() if p.is_dir()]
    assert len(artifact_dirs) == 1
    branch_meta = artifact_dirs[0] / "branch_meta.json"
    assert branch_meta.exists()
    assert "agent/issue-99-fix-stuff" in branch_meta.read_text(encoding="utf-8")
