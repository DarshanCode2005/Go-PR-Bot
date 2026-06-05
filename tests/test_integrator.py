"""Tests for integrator conflict resolution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from go_agent.coder import normalize_llm_patch
from go_agent.config import Settings, clear_settings_cache
from go_agent.git_util import run_git as git_run
from go_agent.integrator import (
    IntegratorError,
    build_merge_messages,
    integrate_file_patches,
    write_integrator_artifact,
)
from go_agent.planner import FixPlan
from go_agent.run_context import create_run_context
from helpers import run_git

FOO_GO = "package pkg\n\nfunc Foo() { return 1 }\n"
BAR_GO = "package pkg\n\nfunc Bar() {}\n"

PATCH_A_SR = """--- SEARCH
func Foo() { return 1 }
+++ REPLACE
func Foo() { return 2 }
"""

PATCH_B_SR = """--- SEARCH
func Foo() { return 1 }
+++ REPLACE
func Foo() { return 3 }
"""

MERGED_SR = """--- SEARCH
func Foo() { return 1 }
+++ REPLACE
func Foo() { return 23 }
"""


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _init_repo(tmp_path: Path, *, with_bar: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init"], cwd=repo)
    run_git(["config", "user.email", "test@example.com"], cwd=repo)
    run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "foo.go").write_text(FOO_GO, encoding="utf-8")
    if with_bar:
        (pkg / "bar.go").write_text(BAR_GO, encoding="utf-8")
    run_git(["add", "."], cwd=repo)
    run_git(["commit", "-m", "init"], cwd=repo)
    return repo


def _plan(files: list[str]) -> FixPlan:
    return FixPlan(
        issue_number=1,
        repo="example/foo",
        files=files,
        steps=["Integrate patches"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Tests pass"],
    )


def _file_patch(path: str, original: str, sr: str):
    return normalize_llm_patch(path, original, sr, _plan([path]))


def test_merge_messages_include_both_hunks():
    patch_a = _file_patch("pkg/foo.go", FOO_GO, PATCH_A_SR)
    patch_b = _file_patch("pkg/foo.go", FOO_GO, PATCH_B_SR)
    messages = build_merge_messages("pkg/foo.go", FOO_GO, [patch_a, patch_b])
    user = messages[-1]["content"]
    assert "Patch 1" in user
    assert "Patch 2" in user
    assert "return 2" in user
    assert "return 3" in user


def test_integrate_applies_disjoint_files_in_order(tmp_path):
    repo = _init_repo(tmp_path, with_bar=True)
    base_sha = git_run(["rev-parse", "HEAD"], cwd=repo)
    bar_patch = normalize_llm_patch(
        "pkg/bar.go",
        BAR_GO,
        "--- SEARCH\nfunc Bar() {}\n+++ REPLACE\nfunc Bar() { return 1 }\n",
        _plan(["pkg/foo.go", "pkg/bar.go"]),
    )
    foo_patch = _file_patch("pkg/foo.go", FOO_GO, PATCH_A_SR)
    result = integrate_file_patches(
        repo,
        [foo_patch, bar_patch],
        _plan(["pkg/foo.go", "pkg/bar.go"]),
        base_sha,
        Settings(),
    )
    assert result.conflicts == []
    assert set(result.files_touched) == {"pkg/foo.go", "pkg/bar.go"}
    check = subprocess.run(
        ["git", "apply", "--check"],
        cwd=repo,
        input=result.resolved_patch,
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stderr


def test_integrate_overlapping_hunks_merge(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    base_sha = git_run(["rev-parse", "HEAD"], cwd=repo)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    plan = _plan(["pkg/foo.go"])
    patch_a = _file_patch("pkg/foo.go", FOO_GO, PATCH_A_SR)
    patch_b = _file_patch("pkg/foo.go", FOO_GO, PATCH_B_SR)

    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", lambda **_: MERGED_SR)

    result = integrate_file_patches(
        repo,
        [patch_a, patch_b],
        plan,
        base_sha,
        Settings(),
    )
    assert len(result.conflicts) == 1
    assert result.conflicts[0].path == "pkg/foo.go"
    assert result.conflicts[0].patch_count == 2
    assert "+func Foo() { return 23 }" in result.resolved_patch
    check = subprocess.run(
        ["git", "apply", "--check"],
        cwd=repo,
        input=result.resolved_patch,
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stderr


def test_integrate_raises_when_merge_fails(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    base_sha = git_run(["rev-parse", "HEAD"], cwd=repo)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    plan = _plan(["pkg/foo.go"])
    patch_a = _file_patch("pkg/foo.go", FOO_GO, PATCH_A_SR)
    patch_b = _file_patch("pkg/foo.go", FOO_GO, PATCH_B_SR)
    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", lambda **_: "not valid merge output")

    with pytest.raises(IntegratorError, match="merge failed"):
        integrate_file_patches(repo, [patch_a, patch_b], plan, base_sha, Settings())


def test_write_integrator_artifact(tmp_path):
    from go_agent.integrator import ConflictResolution, IntegratorResult

    settings = Settings(artifacts_dir=tmp_path / "artifacts")
    ctx = create_run_context(settings)
    result = IntegratorResult(
        resolved_patch="diff --git a/pkg/foo.go b/pkg/foo.go\n",
        conflicts=[
            ConflictResolution(
                path="pkg/foo.go",
                patch_count=2,
                merge_format="search_replace",
            )
        ],
        files_touched=["pkg/foo.go"],
    )
    meta_path = write_integrator_artifact(ctx, result)
    assert meta_path == ctx.artifact_dir / "integrator_meta.json"
    assert (ctx.artifact_dir / "resolved.patch").exists()
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["conflicts"][0]["patch_count"] == 2
