from unittest.mock import patch

from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.github_issues import IssueContext
from helpers import enable_planner_mock

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_main_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "version" in result.stdout
    assert "gin-gonic/gin" in result.stdout


def test_run_help():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    for flag in ("--repo", "--issue", "--dry-run", "--create-pr", "--rag"):
        assert flag in result.stdout


def test_run_missing_repo():
    result = runner.invoke(app, ["run", "--issue", "1"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "repo" in combined.lower() or "missing" in combined.lower()


def test_run_missing_issue():
    result = runner.invoke(app, ["run", "--repo", "gin-gonic/gin"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "issue" in combined.lower() or "missing" in combined.lower()


def test_run_invalid_repo_format():
    result = runner.invoke(app, ["run", "--repo", "bad", "--issue", "1"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "owner/name" in combined.lower() or "gin-gonic" in combined


def test_run_create_pr_conflicts_dry_run():
    result = runner.invoke(
        app,
        ["run", "--repo", "gin-gonic/gin", "--issue", "1", "--create-pr"],
    )
    assert result.exit_code == 2
    assert "no-dry-run" in (result.stdout + result.stderr).lower()


def test_run_dry_run_exits_after_integrate(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("GO_AGENT_WORK_DIR", str(tmp_path / "workspaces"))
    enable_planner_mock(monkeypatch)
    issue_ctx = IssueContext(
        repo="gin-gonic/gin",
        number=1,
        title="Update readme",
        state="open",
    )
    with patch("go_agent.cli.fetch_issue_context", return_value=issue_ctx):
        with patch("go_agent.workspace.github_url", return_value=bare_repo_url):
            result = runner.invoke(app, ["run", "--repo", "gin-gonic/gin", "--issue", "1"])
    assert result.exit_code == 0
    assert "not implemented" not in (result.stdout + result.stderr).lower()
    artifact_dirs = [p for p in (tmp_path / "artifacts").iterdir() if p.is_dir()]
    assert len(artifact_dirs) == 1
    assert (artifact_dirs[0] / "changes.patch").exists()
