"""Tests for go-agent resume command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from go_agent.cli import app
from go_agent.config import clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.orchestrator import compile_graph, get_checkpointer, graph_invoke_config
from go_agent.orchestrator.checkpointer import clear_checkpointer_cache
from go_agent.run_context import create_run_context
from go_agent.run_meta import RunMeta, write_run_meta
from helpers import enable_agent_mocks, init_git_repo

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clear_caches():
    clear_settings_cache()
    clear_checkpointer_cache()
    yield
    clear_settings_cache()
    clear_checkpointer_cache()


def _base_sha(repo_path: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _write_resume_artifacts(
    ctx,
    repo_path: Path,
    *,
    run_id: str,
) -> None:
    write_run_meta(
        ctx,
        RunMeta(
            run_id=run_id,
            repo="gin-gonic/gin",
            issue_number=1,
            artifact_dir=str(ctx.artifact_dir),
            repo_path=str(repo_path),
            workspace_dir=str(ctx.workspace_dir),
        ),
    )
    (ctx.artifact_dir / "issue_context.json").write_text(
        IssueContext(
            repo="gin-gonic/gin",
            number=1,
            title="Update readme",
            state="open",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    (ctx.artifact_dir / "branch_meta.json").write_text(
        json.dumps(
            {
                "branch_name": "agent/issue-1",
                "base_sha": _base_sha(repo_path),
                "default_branch": "main",
                "issue_number": 1,
                "issue_title": "Update readme",
            }
        ),
        encoding="utf-8",
    )
    (ctx.artifact_dir / "scope_hints.json").write_text(
        json.dumps(
            {
                "scope_hints": [],
                "issue_number": 1,
                "repo": "gin-gonic/gin",
                "files": [],
            }
        ),
        encoding="utf-8",
    )


def test_resume_help_lists_run_id():
    result = runner.invoke(app, ["resume", "--help"])
    assert result.exit_code == 0
    assert "--run-id" in result.stdout


def test_main_help_lists_resume():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "resume" in result.stdout


def test_resume_missing_run_id():
    result = runner.invoke(app, ["resume"])
    assert result.exit_code != 0


def test_resume_unknown_run_id(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    result = runner.invoke(app, ["resume", "--run-id", "00000000-0000-0000-0000-000000000000"])
    assert result.exit_code == 2
    assert "not found" in (result.stdout + result.stderr).lower()


def test_resume_already_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    enable_agent_mocks(monkeypatch)
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    ctx = create_run_context()
    _write_resume_artifacts(ctx, repo_path, run_id=ctx.run_id)

    compiled = compile_graph(
        include_test=True,
        include_closed_loop=True,
        checkpointer=get_checkpointer(),
    )
    initial_state = {
        "run_id": ctx.run_id,
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(ctx.artifact_dir),
        "repo_path": str(repo_path),
        "scope_hints": [],
        "issue_context": json.loads((ctx.artifact_dir / "issue_context.json").read_text()),
        "context_bundle": {
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "files": [],
            "total_chars": 0,
            "budget_chars": 12000,
        },
        "branch_meta": json.loads((ctx.artifact_dir / "branch_meta.json").read_text()),
        "iteration": 0,
    }
    compiled.invoke(initial_state, graph_invoke_config(ctx.run_id))

    result = runner.invoke(app, ["resume", "--run-id", ctx.run_id])
    assert result.exit_code == 2
    assert "already complete" in (result.stdout + result.stderr).lower()


def test_resume_happy_path(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    enable_agent_mocks(monkeypatch)
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    ctx = create_run_context()
    _write_resume_artifacts(ctx, repo_path, run_id=ctx.run_id)

    compiled = compile_graph(
        include_test=True,
        include_closed_loop=True,
        checkpointer=get_checkpointer(),
    )
    initial_state = {
        "run_id": ctx.run_id,
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(ctx.artifact_dir),
        "repo_path": str(repo_path),
        "scope_hints": [],
        "issue_context": json.loads((ctx.artifact_dir / "issue_context.json").read_text()),
        "context_bundle": {
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "files": [],
            "total_chars": 0,
            "budget_chars": 12000,
        },
        "branch_meta": {
            "base_sha": _base_sha(repo_path),
            "branch_name": "agent/issue-1",
        },
        "iteration": 0,
    }
    compiled.invoke(
        initial_state,
        graph_invoke_config(ctx.run_id),
        interrupt_after=["plan"],
    )

    result = runner.invoke(app, ["resume", "--run-id", ctx.run_id])
    assert result.exit_code == 0
    log_text = (ctx.artifact_dir / "run.log").read_text(encoding="utf-8")
    assert "Resuming run" in log_text
    assert (ctx.artifact_dir / "lint_result.json").exists()
