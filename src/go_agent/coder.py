"""Coder agent — per-file patch generation from plan and context."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.github_issues import IssueContext
from go_agent.llm_client import complete, llm_available
from go_agent.planner import FixPlan
from go_agent.utils import normalize_file_path
from go_agent.run_context import RunContext

_DIFF_GIT = re.compile(r"^diff --git a/(.+?) b/", re.MULTILINE)
CODER_SYSTEM_PROMPT = """You are a Go coder agent editing exactly one file for a GitHub issue fix.

Output either:
1) SEARCH/REPLACE blocks only (preferred):
--- SEARCH
<exact lines copied from the file>
+++ REPLACE
<replacement lines>
2) OR a unified diff for this file only.

Rules:
- Edit ONLY the file named in the user message
- Do not modify or reference other files
- SEARCH text must match the file exactly
- Make the smallest correct change"""


class CoderError(RuntimeError):
    """Raised when patch generation or validation fails."""


class PlanSlice(BaseModel):
    file_path: str
    steps: list[str]
    test_commands: list[str]
    acceptance_criteria: list[str]


class FilePatch(BaseModel):
    path: str
    format: Literal["search_replace", "unified_diff"]
    patch: str
    search_replace_raw: str | None = None


class CoderArtifact(BaseModel):
    issue_number: int
    repo: str
    files: list[FilePatch] = Field(default_factory=list)
    combined_patch: str
    execution_waves: list[list[str]] = Field(default_factory=list)


def _canonical_path_map(plan: FixPlan) -> dict[str, str]:
    return {normalize_file_path(path): path for path in plan.files}


def _direct_dependencies(plan: FixPlan, file_path: str) -> list[str]:
    normalized = normalize_file_path(file_path)
    for key, deps in plan.file_dependencies.items():
        if normalize_file_path(key) == normalized:
            return list(deps)
    return []


def _dependency_closure(plan: FixPlan, file_path: str) -> list[str]:
    canonical = _canonical_path_map(plan)
    normalized = normalize_file_path(file_path)
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(node: str) -> None:
        for dep in _direct_dependencies(plan, canonical.get(node, node)):
            dep_norm = normalize_file_path(dep)
            if dep_norm in seen:
                continue
            visit(dep_norm)
            if dep_norm not in seen:
                ordered.append(canonical[dep_norm])
                seen.add(dep_norm)

    visit(normalized)
    return ordered


def schedule_coder_waves(plan: FixPlan) -> list[list[str]]:
    """Return topological waves of planned files for parallel/sequential coding."""
    if not plan.files:
        return []

    canonical = _canonical_path_map(plan)
    normalized_files = [normalize_file_path(path) for path in plan.files]
    indegree = {path: 0 for path in normalized_files}
    dependents: dict[str, list[str]] = {path: [] for path in normalized_files}

    for path in plan.files:
        node = normalize_file_path(path)
        for dep in _direct_dependencies(plan, path):
            dep_norm = normalize_file_path(dep)
            if dep_norm not in indegree:
                msg = f"file_dependencies for {path!r} references unknown file {dep!r}"
                raise CoderError(msg)
            indegree[node] += 1
            dependents[dep_norm].append(node)

    waves: list[list[str]] = []
    ready = sorted(path for path, degree in indegree.items() if degree == 0)
    visited = 0

    while ready:
        wave = [canonical[path] for path in ready]
        waves.append(wave)
        visited += len(ready)
        next_ready: list[str] = []
        for path in ready:
            for dependent in dependents[path]:
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    next_ready.append(dependent)
        ready = sorted(next_ready)

    if visited != len(normalized_files):
        msg = "file_dependencies contains a cycle"
        raise CoderError(msg)
    return waves


def _apply_patch_to_content(path: str, content: str, patch: FilePatch) -> str:
    if patch.format == "search_replace" and patch.search_replace_raw:
        blocks = parse_search_replace_blocks(patch.search_replace_raw)
        return apply_search_replace_blocks(content, blocks)
    if not patch.patch.strip():
        return content
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subprocess.run(
            ["git", "init"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "coder@test"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Coder"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        rel = Path(path)
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, text=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "base"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        patch_path = root / "overlay.patch"
        patch_path.write_text(patch.patch, encoding="utf-8")
        result = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            msg = f"failed to overlay dependency patch for {path}: {result.stderr.strip()}"
            raise CoderError(msg)
        return target.read_text(encoding="utf-8")


def _read_file_for_coding(
    repo_path: Path,
    file_path: str,
    settings: Settings,
) -> str:
    return _read_repo_file(repo_path, file_path, settings)


def _dependency_context_for_file(
    repo_path: Path,
    file_path: str,
    completed: dict[str, FilePatch],
    plan: FixPlan,
    settings: Settings,
) -> str | None:
    parts: list[str] = []
    for dep in _dependency_closure(plan, file_path):
        dep_content = _read_repo_file(repo_path, dep, settings)
        dep_patch = completed.get(dep)
        if dep_patch is not None:
            dep_content = _apply_patch_to_content(dep, dep_content, dep_patch)
        parts.append(f"### {dep} (after planned edit)\n{dep_content[:4000]}")
    return "\n\n".join(parts) if parts else None


def assert_file_in_plan(path: str, plan: FixPlan) -> None:
    normalized = normalize_file_path(path)
    allowed = {normalize_file_path(item) for item in plan.files}
    if normalized not in allowed:
        msg = f"file {path!r} is not listed in plan.files"
        raise CoderError(msg)


def extract_paths_from_unified_diff(patch: str) -> set[str]:
    paths = {normalize_file_path(match) for match in _DIFF_GIT.findall(patch)}
    if paths:
        return paths
    for line in patch.splitlines():
        if line.startswith("--- a/"):
            paths.add(normalize_file_path(line.removeprefix("--- a/")))
        elif line.startswith("+++ b/"):
            paths.add(normalize_file_path(line.removeprefix("+++ b/")))
    return {path for path in paths if path and path != "/dev/null"}


def validate_patch_scope(patch: str, allowed: set[str]) -> None:
    if not patch.strip():
        return
    touched = extract_paths_from_unified_diff(patch)
    if not touched:
        msg = "patch does not contain recognizable file paths"
        raise CoderError(msg)
    normalized_allowed = {normalize_file_path(path) for path in allowed}
    for path in touched:
        if path not in normalized_allowed:
            msg = f"patch modifies out-of-plan file: {path}"
            raise CoderError(msg)


def parse_search_replace_blocks(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"--- SEARCH\s*\n(.*?)\n\+\+\+ REPLACE\s*\n(.*?)(?=\n--- SEARCH\s*\n|\Z)",
        re.DOTALL,
    )
    blocks: list[tuple[str, str]] = []
    for match in pattern.finditer(text.strip()):
        search = match.group(1)
        replace = match.group(2).rstrip("\n")
        if not search:
            msg = "SEARCH block must not be empty"
            raise CoderError(msg)
        blocks.append((search, replace))
    if not blocks:
        msg = "no SEARCH/REPLACE blocks found in coder output"
        raise CoderError(msg)
    return blocks


def apply_search_replace_blocks(content: str, blocks: list[tuple[str, str]]) -> str:
    updated = content
    for search, replace in blocks:
        count = updated.count(search)
        if count != 1:
            msg = f"SEARCH block must match exactly once (found {count} matches)"
            raise CoderError(msg)
        updated = updated.replace(search, replace, 1)
    return updated


def unified_diff_for_file(path: str, original: str, modified: str) -> str:
    if original == modified:
        return ""
    rel = Path(path)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_file = root / "old" / rel
        new_file = root / "new" / rel
        old_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text(original, encoding="utf-8")
        new_file.write_text(modified, encoding="utf-8")
        result = subprocess.run(
            ["git", "diff", "--no-index", "--", str(old_file), str(new_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode not in {0, 1}:
            msg = f"git diff failed for {path}: {result.stderr.strip()}"
            raise CoderError(msg)
        body = result.stdout
        if not body.strip():
            return ""
        rewritten: list[str] = []
        for line in body.splitlines(keepends=True):
            if line.startswith("diff --git "):
                rewritten.append(f"diff --git a/{path} b/{path}\n")
            elif line.startswith("--- "):
                rewritten.append(f"--- a/{path}\n")
            elif line.startswith("+++ "):
                rewritten.append(f"+++ b/{path}\n")
            else:
                rewritten.append(line)
        return "".join(rewritten)


def _is_unified_diff(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("--- SEARCH"):
        return False
    if stripped.startswith("diff --git"):
        return True
    return stripped.startswith("--- a/") or stripped.startswith("--- /dev/null")


def normalize_llm_patch(
    path: str,
    original: str,
    llm_output: str,
    plan: FixPlan,
) -> FilePatch:
    assert_file_in_plan(path, plan)
    text = llm_output.strip()
    if _is_unified_diff(text):
        validate_patch_scope(text, set(plan.files))
        return FilePatch(path=path, format="unified_diff", patch=text)
    blocks = parse_search_replace_blocks(text)
    modified = apply_search_replace_blocks(original, blocks)
    patch = unified_diff_for_file(path, original, modified)
    if patch:
        validate_patch_scope(patch, set(plan.files))
    return FilePatch(
        path=path,
        format="search_replace",
        patch=patch,
        search_replace_raw=text,
    )


def plan_slice_for_file(plan: FixPlan, file_path: str) -> PlanSlice:
    assert_file_in_plan(file_path, plan)
    return PlanSlice(
        file_path=normalize_file_path(file_path),
        steps=list(plan.steps),
        test_commands=list(plan.test_commands),
        acceptance_criteria=list(plan.acceptance_criteria),
    )


def _bundle_entry_for_file(
    context_bundle: ContextBundle,
    file_path: str,
) -> ContextFileEntry | None:
    normalized = normalize_file_path(file_path)
    for entry in context_bundle.files:
        if normalize_file_path(entry.path) == normalized:
            return entry
    return None


def build_coder_messages(
    issue: IssueContext,
    plan_slice: PlanSlice,
    file_content: str,
    bundle_entry: ContextFileEntry | None,
    *,
    correction: str | None = None,
    dependency_context: str | None = None,
) -> list[dict[str, str]]:
    steps = "\n".join(f"- {step}" for step in plan_slice.steps)
    tests = "\n".join(f"- {command}" for command in plan_slice.test_commands)
    criteria = "\n".join(f"- {item}" for item in plan_slice.acceptance_criteria)
    context_bits: list[str] = [
        f"Issue #{issue.number}: {issue.title}",
        f"Target file: {plan_slice.file_path}",
        f"Plan steps:\n{steps}",
        f"Test commands:\n{tests}",
        f"Acceptance criteria:\n{criteria}",
        f"Current file content:\n{file_content}",
    ]
    if bundle_entry is not None:
        context_bits.append(
            f"Context note ({bundle_entry.content_tier}): {bundle_entry.rationale}\n"
            f"{bundle_entry.content[:2000]}"
        )
    if dependency_context:
        context_bits.append(f"Dependency context:\n{dependency_context}")
    if correction:
        context_bits.append(correction)
    return [
        {"role": "system", "content": CODER_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(context_bits)},
    ]


def _read_repo_file(repo_path: Path, file_path: str, settings: Settings) -> str:
    full_path = repo_path / file_path
    if not full_path.is_file():
        msg = f"planned file not found in repo: {file_path}"
        raise CoderError(msg)
    content = full_path.read_text(encoding="utf-8")
    if len(content) > settings.coder_max_file_chars:
        msg = f"file {file_path} exceeds coder_max_file_chars"
        raise CoderError(msg)
    return content


def generate_file_patch(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    file_path: str,
    context_bundle: ContextBundle,
    settings: Settings,
    logger: logging.Logger | None = None,
    *,
    file_content: str | None = None,
    dependency_context: str | None = None,
) -> FilePatch:
    """Generate a normalized unified diff for one planned file."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise CoderError("LLM API key required for coder")

    normalized_path = normalize_file_path(file_path)
    original = (
        file_content
        if file_content is not None
        else _read_repo_file(repo_path, normalized_path, settings)
    )
    plan_slice = plan_slice_for_file(plan, normalized_path)
    bundle_entry = _bundle_entry_for_file(context_bundle, normalized_path)
    messages = build_coder_messages(
        issue,
        plan_slice,
        original,
        bundle_entry,
        dependency_context=dependency_context,
    )

    try:
        content = complete(messages, tier="fast", settings=settings)
        if not content:
            raise CoderError("LLM completion failed")
        file_patch = normalize_llm_patch(normalized_path, original, content, plan)
        log.info("Coder patch generated for %s (%s)", normalized_path, file_patch.format)
        return file_patch
    except CoderError as first_error:
        log.warning("Coder first attempt failed for %s: %s", normalized_path, first_error)
        retry_messages = build_coder_messages(
            issue,
            plan_slice,
            original,
            bundle_entry,
            correction=(
                f"Previous output was invalid: {first_error}. "
                "Return SEARCH/REPLACE blocks or a unified diff for this file only."
            ),
            dependency_context=dependency_context,
        )
        content = complete(retry_messages, tier="fast", settings=settings)
        if not content:
            raise CoderError(f"Coder failed after retry for {normalized_path}") from first_error
        try:
            return normalize_llm_patch(normalized_path, original, content, plan)
        except CoderError as retry_error:
            raise CoderError(
                f"Coder failed after retry for {normalized_path}: {retry_error}"
            ) from retry_error


def combine_file_patches(file_patches: list[FilePatch]) -> str:
    parts = [item.patch.strip() for item in file_patches if item.patch.strip()]
    if not parts:
        return ""
    return "\n".join(parts) + "\n"


def _generate_file_patch_for_wave(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    file_path: str,
    context_bundle: ContextBundle,
    settings: Settings,
    completed: dict[str, FilePatch],
    logger: logging.Logger,
) -> FilePatch:
    file_content = _read_file_for_coding(repo_path, file_path, settings)
    dependency_context = _dependency_context_for_file(
        repo_path,
        file_path,
        completed,
        plan,
        settings,
    )
    return generate_file_patch(
        repo_path,
        issue,
        plan,
        file_path,
        context_bundle,
        settings,
        logger=logger,
        file_content=file_content,
        dependency_context=dependency_context,
    )


def build_proposed_patch(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    context_bundle: ContextBundle,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> CoderArtifact:
    """Generate per-file patches for all plan files and merge into one diff."""
    log = logger or logging.getLogger("go_agent")
    if not plan.files:
        raise CoderError("plan.files is empty; nothing to code")

    waves = schedule_coder_waves(plan)
    completed: dict[str, FilePatch] = {}

    for wave_index, wave in enumerate(waves):
        log.info("Coder wave %d: %d files (parallel)", wave_index, len(wave))
        if len(wave) == 1:
            path = wave[0]
            completed[path] = _generate_file_patch_for_wave(
                repo_path,
                issue,
                plan,
                path,
                context_bundle,
                settings,
                dict(completed),
                log,
            )
            continue

        max_workers = max(1, min(settings.coder_max_workers, len(wave)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _generate_file_patch_for_wave,
                    repo_path,
                    issue,
                    plan,
                    path,
                    context_bundle,
                    settings,
                    dict(completed),
                    log,
                ): path
                for path in wave
            }
            try:
                for future in as_completed(futures):
                    path = futures[future]
                    completed[path] = future.result()
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise

    file_patches = [completed[path] for path in plan.files if path in completed]
    combined = combine_file_patches(file_patches)
    if not combined.strip():
        raise CoderError("coder generated no changes for any planned file")
    log.info("Coder produced %d file patch(es) in %d wave(s)", len(file_patches), len(waves))
    return CoderArtifact(
        issue_number=issue.number,
        repo=issue.repo,
        files=file_patches,
        combined_patch=combined,
        execution_waves=waves,
    )


def write_coder_artifact(ctx: RunContext, artifact: CoderArtifact) -> Path:
    patch_path = ctx.artifact_dir / "proposed.patch"
    patch_path.write_text(artifact.combined_patch, encoding="utf-8")
    meta_path = ctx.artifact_dir / "coder_meta.json"
    meta_path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return patch_path
