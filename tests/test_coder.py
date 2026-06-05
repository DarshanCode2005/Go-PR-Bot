"""Tests for coder agent patch generation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
import pytest

from go_agent.coder import (
    CoderError,
    apply_search_replace_blocks,
    build_proposed_patch,
    extract_paths_from_unified_diff,
    generate_file_patch,
    normalize_llm_patch,
    parse_search_replace_blocks,
    validate_patch_scope,
    write_coder_artifact,
)
from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.github_issues import IssueContext
from go_agent.planner import FixPlan
from go_agent.run_context import create_run_context
from helpers import run_git

FOO_GO = "package pkg\n\nfunc Foo() {}\n"
FOO_GO_PATCHED = "package pkg\n\nfunc Foo() int { return 1 }\n"

MOCK_FOO_SR = """--- SEARCH
func Foo() {}
+++ REPLACE
func Foo() int { return 1 }
"""

OUT_OF_PLAN_DIFF = """diff --git a/pkg/bar.go b/pkg/bar.go
--- a/pkg/bar.go
+++ b/pkg/bar.go
@@ -1 +1 @@
-old
+new
"""


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _init_fixture_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init"], cwd=repo)
    run_git(["config", "user.email", "test@example.com"], cwd=repo)
    run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "foo.go").write_text(FOO_GO, encoding="utf-8")
    run_git(["add", "."], cwd=repo)
    run_git(["commit", "-m", "init"], cwd=repo)
    return repo


def _fixture_plan() -> FixPlan:
    return FixPlan(
        issue_number=1,
        repo="example/foo",
        files=["pkg/foo.go"],
        steps=["Change Foo return type"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Tests pass"],
    )


def _fixture_issue() -> IssueContext:
    return IssueContext(
        repo="example/foo",
        number=1,
        title="Fix Foo",
        state="open",
    )


def _fixture_bundle() -> ContextBundle:
    return ContextBundle(
        issue_number=1,
        repo="example/foo",
        files=[
            ContextFileEntry(
                path="pkg/foo.go",
                content_tier="full",
                content=FOO_GO,
                rationale="primary file",
                graph_distance=0,
                char_count=len(FOO_GO),
            )
        ],
        budget_chars=80000,
        total_chars=len(FOO_GO),
    )


def test_parse_search_replace_blocks():
    text = (
        "--- SEARCH\n"
        "alpha\n"
        "+++ REPLACE\n"
        "beta\n"
        "\n"
        "--- SEARCH\n"
        "gamma\n"
        "+++ REPLACE\n"
        "delta\n"
    )
    blocks = parse_search_replace_blocks(text)
    assert blocks == [("alpha", "beta"), ("gamma", "delta")]


def test_apply_search_replace_updates_content():
    blocks = parse_search_replace_blocks(MOCK_FOO_SR)
    updated = apply_search_replace_blocks(FOO_GO, blocks)
    assert updated == FOO_GO_PATCHED


def test_unified_diff_extract_paths():
    paths = extract_paths_from_unified_diff(OUT_OF_PLAN_DIFF)
    assert paths == {"pkg/bar.go"}


def test_validate_patch_scope_rejects_out_of_plan():
    with pytest.raises(CoderError, match="out-of-plan"):
        validate_patch_scope(OUT_OF_PLAN_DIFF, {"pkg/foo.go"})


def test_normalize_llm_patch_from_search_replace():
    patch = normalize_llm_patch("pkg/foo.go", FOO_GO, MOCK_FOO_SR, _fixture_plan())
    assert patch.format == "search_replace"
    assert "pkg/foo.go" in patch.patch
    assert "+func Foo() int" in patch.patch


def test_generate_file_patch_mock_transport(tmp_path, monkeypatch):
    repo = _init_fixture_repo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    settings = Settings()

    def transport(**kwargs):
        return MOCK_FOO_SR

    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", transport)

    file_patch = generate_file_patch(
        repo,
        _fixture_issue(),
        _fixture_plan(),
        "pkg/foo.go",
        _fixture_bundle(),
        settings,
    )
    assert file_patch.path == "pkg/foo.go"
    assert "+func Foo() int" in file_patch.patch


def test_build_proposed_patch_applies_cleanly(tmp_path, monkeypatch):
    repo = _init_fixture_repo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    settings = Settings()

    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", lambda **_: MOCK_FOO_SR)

    artifact = build_proposed_patch(
        repo,
        _fixture_issue(),
        _fixture_plan(),
        _fixture_bundle(),
        settings,
    )
    assert artifact.combined_patch.strip()
    check = subprocess.run(
        ["git", "apply", "--check"],
        cwd=repo,
        input=artifact.combined_patch,
        text=True,
        capture_output=True,
    )
    assert check.returncode == 0, check.stderr


def test_write_coder_artifact(tmp_path):
    from go_agent.coder import CoderArtifact, FilePatch

    settings = Settings(artifacts_dir=tmp_path / "artifacts")
    ctx = create_run_context(settings)

    coder_artifact = CoderArtifact(
        issue_number=1,
        repo="example/foo",
        files=[
            FilePatch(
                path="pkg/foo.go",
                format="search_replace",
                patch="diff --git a/pkg/foo.go b/pkg/foo.go\n",
            )
        ],
        combined_patch="diff --git a/pkg/foo.go b/pkg/foo.go\n",
    )
    patch_path = write_coder_artifact(ctx, coder_artifact)
    assert patch_path == ctx.artifact_dir / "proposed.patch"
    assert patch_path.exists()
    meta_path = ctx.artifact_dir / "coder_meta.json"
    assert meta_path.exists()
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    assert payload["repo"] == "example/foo"
    assert payload["files"][0]["path"] == "pkg/foo.go"
