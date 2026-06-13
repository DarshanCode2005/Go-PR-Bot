"""Tests for coder agent patch generation."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
import pytest
from pydantic import ValidationError

from go_agent.coder import (
    CoderError,
    _dependency_context_for_file,
    _read_file_for_coding,
    apply_search_replace_blocks,
    build_proposed_patch,
    collect_coder_anchors,
    extract_paths_from_unified_diff,
    file_needs_coder_patch,
    generate_file_patch,
    is_invalid_go_patch_snippet,
    normalize_llm_patch,
    parse_search_replace_blocks,
    plan_slice_for_file,
    sanitize_llm_patch_output,
    schedule_coder_waves,
    slice_file_for_coder_prompt,
    validate_patch_scope,
    write_coder_artifact,
)
from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.coder import PlanSlice
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


def test_file_needs_coder_patch_skips_unrelated_test_files():
    plan = FixPlan(
        issue_number=1348,
        repo="go-playground/validator",
        files=["baked_in.go", "validator_test.go", "benchmarks_test.go"],
        steps=[
            "Fix unix_addr in baked_in.go",
            "Add a test case in validator_test.go",
        ],
        test_commands=["go test -run TestUnixAddrValidation -count=1"],
        acceptance_criteria=["TestUnixAddrValidation passes"],
    )
    bundle = ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=80,
        files=[
            ContextFileEntry(
                path="validator_test.go",
                content_tier="snippet",
                content="func TestUnixAddrValidation(t *testing.T) {}",
                rationale="regression",
                graph_distance=0,
                char_count=40,
            ),
            ContextFileEntry(
                path="benchmarks_test.go",
                content_tier="snippet",
                content="func BenchmarkValidate(b *testing.B) {}",
                rationale="benchmarks",
                graph_distance=1,
                char_count=35,
            ),
        ],
    )
    assert file_needs_coder_patch(plan, "baked_in.go", bundle) is True
    assert file_needs_coder_patch(plan, "validator_test.go", bundle) is False
    assert file_needs_coder_patch(plan, "benchmarks_test.go", bundle) is False


def test_file_needs_coder_patch_patches_test_when_steps_update_expectations():
    plan = FixPlan(
        issue_number=1,
        repo="example/foo",
        files=["pkg/foo.go", "pkg/foo_test.go"],
        steps=[
            "Fix Foo in pkg/foo.go",
            "Update test expectations in pkg/foo_test.go for new return type",
        ],
        test_commands=["go test ./pkg -count=1"],
        acceptance_criteria=["Tests pass"],
    )
    assert file_needs_coder_patch(plan, "pkg/foo_test.go", None) is True


def test_file_needs_coder_patch_patches_test_only_plan():
    plan = FixPlan(
        issue_number=1,
        repo="example/foo",
        files=["pkg/foo_test.go"],
        steps=["Add table-driven cases to pkg/foo_test.go"],
        test_commands=["go test ./pkg -count=1"],
        acceptance_criteria=["Tests pass"],
    )
    assert file_needs_coder_patch(plan, "pkg/foo_test.go", None) is True


def test_plan_slice_for_file_filters_steps_to_target_test_file():
    plan = FixPlan(
        issue_number=1348,
        repo="go-playground/validator",
        files=["baked_in.go", "validator_test.go"],
        steps=[
            "Fix unix_addr in baked_in.go",
            "Add a test case in validator_test.go",
        ],
        test_commands=["go test -count=1"],
        acceptance_criteria=["TestUnixAddrValidation passes"],
    )
    prod_slice = plan_slice_for_file(plan, "baked_in.go")
    test_slice = plan_slice_for_file(plan, "validator_test.go")
    assert any("baked_in.go" in step for step in prod_slice.steps)
    assert all("validator_test.go" in step for step in test_slice.steps)


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


def test_is_invalid_go_patch_snippet_detects_hallucinations():
    corrupt = (
        "func isUnixAddrResolvable(fl FieldLevel) bool {\n"
        "\treturn false\n"
        "}\n\n"
        "package validator\n\n"
        "import (\n"
        '\t"os"\n'
        ")\n"
    )
    assert is_invalid_go_patch_snippet(corrupt)
    assert is_invalid_go_patch_snippet("func Foo() {}\n\n// ... [lines 10-20 of 100] ...")
    assert is_invalid_go_patch_snippet("func Foo() {\n\t// ... (rest of the function remains the same)\n}")
    assert not is_invalid_go_patch_snippet("package validator\n\nimport (\n\t\"os\"\n)")
    assert not is_invalid_go_patch_snippet("func Foo() int { return 1 }")


def test_apply_search_replace_rejects_midfile_package_in_replace():
    llm_output = (
        "--- SEARCH\n"
        "func Foo() {}\n"
        "+++ REPLACE\n"
        "func Foo() {}\n\n"
        "package validator\n"
    )
    blocks = parse_search_replace_blocks(llm_output)
    with pytest.raises(CoderError, match="invalid Go"):
        apply_search_replace_blocks(FOO_GO, blocks)


def test_apply_search_replace_falls_back_to_function_name(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    original = (
        "// isUnixAddrResolvable validates unix addresses.\n"
        "func isUnixAddrResolvable(fl FieldLevel) bool {\n"
        '\t_, err := net.ResolveUnixAddr("unix", fl.Field().String())\n'
        "\n"
        "\treturn err == nil\n"
        "}\n"
    )
    llm_output = (
        "--- SEARCH\n"
        "func isUnixAddrResolvable(fl FieldLevel) bool {\n"
        "\treturn false\n"
        "}\n"
        "+++ REPLACE\n"
        "// isUnixAddrResolvable validates unix addresses.\n"
        "func isUnixAddrResolvable(fl FieldLevel) bool {\n"
        '\taddr := fl.Field().String()\n'
        '\tif addr == "" {\n'
        "\t\treturn true\n"
        "\t}\n"
        '\t_, err := net.ResolveUnixAddr("unix", addr)\n'
        "\n"
        "\treturn err == nil\n"
        "}\n"
    )
    blocks = parse_search_replace_blocks(llm_output)
    updated = apply_search_replace_blocks(original, blocks)
    assert 'if addr == ""' in updated
    assert "return false" not in updated


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


def _multi_file_plan(
    files: list[str],
    *,
    file_dependencies: dict[str, list[str]] | None = None,
) -> FixPlan:
    return FixPlan(
        issue_number=1,
        repo="example/foo",
        files=files,
        steps=["Apply multi-file fix"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Tests pass"],
        file_dependencies=file_dependencies or {},
    )


def test_schedule_coder_waves_disjoint():
    plan = _multi_file_plan(["pkg/a.go", "pkg/b.go", "pkg/c.go"])
    assert schedule_coder_waves(plan) == [["pkg/a.go", "pkg/b.go", "pkg/c.go"]]


def test_schedule_coder_waves_chain():
    plan = _multi_file_plan(
        ["pkg/a.go", "pkg/b.go", "pkg/c.go"],
        file_dependencies={
            "pkg/b.go": ["pkg/a.go"],
            "pkg/c.go": ["pkg/b.go"],
        },
    )
    assert schedule_coder_waves(plan) == [["pkg/a.go"], ["pkg/b.go"], ["pkg/c.go"]]


def test_schedule_coder_waves_diamond():
    plan = _multi_file_plan(
        ["pkg/a.go", "pkg/b.go", "pkg/c.go", "pkg/d.go"],
        file_dependencies={
            "pkg/b.go": ["pkg/a.go"],
            "pkg/c.go": ["pkg/a.go"],
            "pkg/d.go": ["pkg/b.go", "pkg/c.go"],
        },
    )
    assert schedule_coder_waves(plan) == [
        ["pkg/a.go"],
        ["pkg/b.go", "pkg/c.go"],
        ["pkg/d.go"],
    ]


def test_schedule_coder_waves_cycle_raises():
    with pytest.raises(ValidationError):
        _multi_file_plan(
            ["pkg/a.go", "pkg/b.go"],
            file_dependencies={
                "pkg/a.go": ["pkg/b.go"],
                "pkg/b.go": ["pkg/a.go"],
            },
        )


class _ConcurrentTransport:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.order: list[str] = []

    def __call__(self, *, messages, model=None, temperature=None) -> str:
        _ = model, temperature
        user = messages[-1]["content"]
        target = user.split("Target file: ", 1)[1].split("\n", 1)[0]
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.order.append(target)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        marker = "Current file content:\n"
        content = user.split(marker, 1)[1].split("\n\n", 1)[0]
        first = content.splitlines()[0] if content.splitlines() else "x"
        return f"--- SEARCH\n{first}\n+++ REPLACE\n{first}.\n"


def _init_multi_file_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(["init"], cwd=repo)
    run_git(["config", "user.email", "test@example.com"], cwd=repo)
    run_git(["config", "user.name", "Test"], cwd=repo)
    (repo / "go.mod").write_text("module example.com/foo\n", encoding="utf-8")
    pkg = repo / "pkg"
    pkg.mkdir()
    for name in ("a.go", "b.go", "c.go"):
        (pkg / name).write_text(f"package pkg\n\n// {name}\n", encoding="utf-8")
    run_git(["add", "."], cwd=repo)
    run_git(["commit", "-m", "init"], cwd=repo)
    return repo


def test_build_proposed_patch_parallel_disjoint(tmp_path, monkeypatch):
    repo = _init_multi_file_repo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    settings = Settings(coder_max_workers=3)
    transport = _ConcurrentTransport()
    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", transport)

    plan = _multi_file_plan(["pkg/a.go", "pkg/b.go", "pkg/c.go"])
    artifact = build_proposed_patch(
        repo,
        _fixture_issue(),
        plan,
        ContextBundle(
            issue_number=1,
            repo="example/foo",
            files=[],
            budget_chars=80000,
            total_chars=0,
        ),
        settings,
    )
    assert artifact.execution_waves == [["pkg/a.go", "pkg/b.go", "pkg/c.go"]]
    assert transport.max_active >= 2


def test_build_proposed_patch_sequential_deps(tmp_path, monkeypatch):
    repo = _init_multi_file_repo(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    clear_settings_cache()
    settings = Settings()
    transport = _ConcurrentTransport()
    monkeypatch.setattr("go_agent.llm_client._TRANSPORT", transport)

    plan = _multi_file_plan(
        ["pkg/a.go", "pkg/b.go", "pkg/c.go"],
        file_dependencies={
            "pkg/b.go": ["pkg/a.go"],
            "pkg/c.go": ["pkg/b.go"],
        },
    )
    artifact = build_proposed_patch(
        repo,
        _fixture_issue(),
        plan,
        ContextBundle(
            issue_number=1,
            repo="example/foo",
            files=[],
            budget_chars=80000,
            total_chars=0,
        ),
        settings,
    )
    assert artifact.execution_waves == [["pkg/a.go"], ["pkg/b.go"], ["pkg/c.go"]]
    assert transport.order.index("pkg/a.go") < transport.order.index("pkg/b.go")
    assert transport.order.index("pkg/b.go") < transport.order.index("pkg/c.go")


def test_read_file_with_dependency_overlay(tmp_path):
    repo = _init_fixture_repo(tmp_path)
    (repo / "pkg" / "bar.go").write_text("package pkg\n\n// uses Foo\n", encoding="utf-8")
    settings = Settings()
    plan = _multi_file_plan(
        ["pkg/foo.go", "pkg/bar.go"],
        file_dependencies={"pkg/bar.go": ["pkg/foo.go"]},
    )
    foo_patch = normalize_llm_patch("pkg/foo.go", FOO_GO, MOCK_FOO_SR, plan)
    completed = {"pkg/foo.go": foo_patch}
    context = _dependency_context_for_file(
        repo,
        "pkg/bar.go",
        completed,
        plan,
        settings,
    )
    assert context is not None
    assert "func Foo() int { return 1 }" in context
    assert _read_file_for_coding(repo, "pkg/bar.go", settings) == "package pkg\n\n// uses Foo\n"


def test_collect_coder_anchors_from_plan():
    plan_slice = PlanSlice(
        file_path="validator_test.go",
        steps=["Modify `isUnixAddrResolvable` in baked_in.go"],
        test_commands=["go test -race -run TestUnixDomainSocketExistsValidation ./..."],
        acceptance_criteria=[],
    )
    anchors = collect_coder_anchors(plan_slice)
    assert "TestUnixDomainSocketExistsValidation" in anchors
    assert "isUnixAddrResolvable" in anchors


def test_slice_file_for_coder_prompt_keeps_small_files():
    content = "package main\n\nfunc Foo() {}\n"
    plan_slice = PlanSlice(
        file_path="main.go",
        steps=["Change Foo"],
        test_commands=[],
        acceptance_criteria=[],
    )
    sliced, truncated = slice_file_for_coder_prompt(content, plan_slice, 60000)
    assert sliced == content
    assert truncated is False


def test_slice_file_for_coder_prompt_extracts_test_region():
    filler = "// filler line\n" * 8000
    target = (
        "func TestUnixDomainSocketExistsValidation(t *testing.T) {\n"
        "\terr := validate.Var(\"/tmp/x.sock\", \"unix_addr\")\n"
        "\t_ = err\n"
        "}\n"
    )
    content = f"package validator\n\n{filler}{target}"
    plan_slice = PlanSlice(
        file_path="validator_test.go",
        steps=["Update TestUnixDomainSocketExistsValidation for unix_addr"],
        test_commands=["go test -run TestUnixDomainSocketExistsValidation ./..."],
        acceptance_criteria=[],
    )
    sliced, truncated = slice_file_for_coder_prompt(content, plan_slice, 60000)
    assert truncated is True
    assert len(sliced) <= 60000
    assert "TestUnixDomainSocketExistsValidation" in sliced
    assert "package validator" in sliced


def test_read_large_file_no_longer_raises(tmp_path):
    repo = _init_fixture_repo(tmp_path)
    big = "package pkg\n\n" + ("// x\n" * 20000)
    (repo / "pkg" / "big.go").write_text(big, encoding="utf-8")
    settings = Settings(coder_max_file_chars=60000)
    content = _read_file_for_coding(repo, "pkg/big.go", settings)
    assert len(content) > settings.coder_max_file_chars


def test_sanitize_llm_patch_output_strips_fences():
    raw = """```diff
--- SEARCH
func Foo() {}
+++ REPLACE
func Foo() int { return 1 }
```"""
    cleaned = sanitize_llm_patch_output(raw)
    assert "```" not in cleaned
    assert "--- SEARCH" in cleaned


def test_normalize_llm_patch_accepts_fenced_search_replace():
    original = "func isUnixAddrResolvable(fl FieldLevel) bool {\n\t_, err := net.ResolveUnixAddr(\"unix\", fl.Field().String())\n\n\treturn err == nil\n}\n"
    fenced = """```diff
--- SEARCH
func isUnixAddrResolvable(fl FieldLevel) bool {
    _, err := net.ResolveUnixAddr("unix", fl.Field().String())

    return err == nil
}
+++ REPLACE
func isUnixAddrResolvable(fl FieldLevel) bool {
    return isSocketPath(fl.Field().String())
}
```"""
    plan = FixPlan(
        issue_number=1,
        repo="go-playground/validator",
        files=["baked_in.go"],
        steps=["Fix unix addr"],
        test_commands=["go test ./..."],
        acceptance_criteria=["Tests pass"],
    )
    patch = normalize_llm_patch("baked_in.go", original, fenced, plan)
    assert "isSocketPath" in patch.patch
    assert "```" not in patch.patch


def test_apply_search_replace_spaces_to_tabs():
    original = "func Foo() {\n\treturn 1\n}\n"
    blocks = parse_search_replace_blocks("""--- SEARCH
func Foo() {
    return 1
}
+++ REPLACE
func Foo() {
    return 2
}
""")
    updated = apply_search_replace_blocks(original, blocks)
    assert "return 2" in updated
    assert "\treturn 2" in updated
