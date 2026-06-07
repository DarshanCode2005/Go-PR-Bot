import logging
import uuid
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.github_issues import IssueContext
from helpers import enable_planner_mock, list_run_artifact_dirs
from go_agent.config import Settings, clear_settings_cache
from go_agent.logging_config import configure_run_logging
from go_agent.run_context import create_run_context

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_run_id_is_uuid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    uuid.UUID(ctx.run_id)


def test_artifact_dir_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    assert ctx.artifact_dir.exists()
    assert ctx.workspace_dir.exists()
    assert ctx.log_path == ctx.artifact_dir / "run.log"


def test_run_log_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)
    logger.info("test message")

    for handler in logging.getLogger("go_agent").handlers:
        handler.flush()

    content = ctx.log_path.read_text(encoding="utf-8")
    assert "test message" in content
    assert ctx.run_id in content


def test_bare_logger_includes_run_id(tmp_path, monkeypatch):
    """Modules that call getLogger('go_agent') directly must not raise KeyError."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        artifacts_dir=tmp_path / "artifacts",
        work_dir=tmp_path / "workspaces",
    )
    ctx = create_run_context(settings)
    configure_run_logging(ctx)

    bare_logger = logging.getLogger("go_agent")
    bare_logger.info("bare logger message")

    for handler in bare_logger.handlers:
        handler.flush()

    content = ctx.log_path.read_text(encoding="utf-8")
    assert "bare logger message" in content
    assert ctx.run_id in content


def test_cli_run_creates_artifact_dir(tmp_path, monkeypatch, bare_repo_url: str):
    monkeypatch.chdir(tmp_path)
    artifacts = tmp_path / "artifacts"
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(artifacts))
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

    subdirs = list_run_artifact_dirs(artifacts)
    assert len(subdirs) == 1
    run_log = subdirs[0] / "run.log"
    assert run_log.exists()
    log_text = run_log.read_text(encoding="utf-8")
    assert "Starting run" in log_text
    assert "Dry run complete" in log_text
    assert (subdirs[0] / "proposed.patch").exists()
    assert (subdirs[0] / "integrator_meta.json").exists()
    assert (subdirs[0] / "changes.patch").exists()
    assert (subdirs[0] / "test_result.json").exists()
    assert (subdirs[0] / "lint_result.json").exists()
