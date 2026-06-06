"""Tests for LangGraph SqliteSaver checkpointer and resume."""

from __future__ import annotations

from go_agent.config import clear_settings_cache
from go_agent.orchestrator import compile_graph
from go_agent.orchestrator.checkpointer import (
    checkpoints_db_path,
    clear_checkpointer_cache,
    create_checkpointer,
    get_graph_state,
    graph_invoke_config,
    is_run_complete,
)
from go_agent.run_context import create_run_context
from go_agent.run_meta import RunMeta, write_run_meta
from helpers import enable_agent_mocks, init_git_repo


def _base_sha(repo_path):
    import subprocess

    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_checkpoints_db_path_under_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    clear_checkpointer_cache()
    path = checkpoints_db_path()
    assert path.endswith("artifacts/checkpoints/checkpoints.db")


def test_create_checkpointer_creates_db_file(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    clear_checkpointer_cache()
    db_path = checkpoints_db_path()
    create_checkpointer(db_path)
    assert __import__("pathlib").Path(db_path).is_file()


def test_resume_continues_after_interrupt(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    init_git_repo(repo_path, files={"README.md": "hello\n"})
    artifact_dir = tmp_path / "artifacts" / "run-cp"
    artifact_dir.mkdir(parents=True)
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    clear_checkpointer_cache()
    enable_agent_mocks(monkeypatch)

    run_id = "run-cp"
    checkpointer = create_checkpointer(checkpoints_db_path())
    compiled = compile_graph(include_test=True, checkpointer=checkpointer)
    initial_state = {
        "run_id": run_id,
        "repo": "gin-gonic/gin",
        "issue_number": 1,
        "artifact_dir": str(artifact_dir),
        "repo_path": str(repo_path),
        "scope_hints": [],
        "issue_context": {
            "repo": "gin-gonic/gin",
            "number": 1,
            "title": "Update readme",
            "state": "open",
        },
        "context_bundle": {
            "repo": "gin-gonic/gin",
            "issue_number": 1,
            "files": [],
            "total_chars": 0,
            "budget_chars": 12000,
        },
        "branch_meta": {"base_sha": _base_sha(repo_path), "branch_name": "agent/issue-1"},
        "iteration": 0,
    }

    compiled.invoke(
        initial_state,
        graph_invoke_config(run_id),
        interrupt_after=["plan"],
    )
    snapshot = get_graph_state(compiled, run_id)
    assert not is_run_complete(snapshot)
    assert "code" in snapshot.next

    final_state = compiled.invoke(None, graph_invoke_config(run_id))
    assert final_state["last_node"] == "lint"
    assert (artifact_dir / "test_result.json").exists()
    assert (artifact_dir / "lint_result.json").exists()


def test_write_run_meta_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("GO_AGENT_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    clear_settings_cache()
    ctx = create_run_context()
    path = write_run_meta(
        ctx,
        RunMeta(
            run_id=ctx.run_id,
            repo="gin-gonic/gin",
            issue_number=1,
            artifact_dir=str(ctx.artifact_dir),
            repo_path=str(tmp_path / "repo"),
            workspace_dir=str(ctx.workspace_dir),
        ),
    )
    assert path == ctx.artifact_dir / "run_meta.json"
    assert path.exists()
