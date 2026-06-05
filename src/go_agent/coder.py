"""Coder agent — per-file patch generation from plan and context."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.github_issues import IssueContext
from go_agent.llm_client import complete, llm_available
from go_agent.planner import FixPlan
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


def _normalize_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("/"):
        normalized = normalized[1:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def assert_file_in_plan(path: str, plan: FixPlan) -> None:
    normalized = _normalize_path(path)
    allowed = {_normalize_path(item) for item in plan.files}
    if normalized not in allowed:
        msg = f"file {path!r} is not listed in plan.files"
        raise CoderError(msg)


def extract_paths_from_unified_diff(patch: str) -> set[str]:
    paths = {_normalize_path(match) for match in _DIFF_GIT.findall(patch)}
    if paths:
        return paths
    for line in patch.splitlines():
        if line.startswith("--- a/"):
            paths.add(_normalize_path(line.removeprefix("--- a/")))
        elif line.startswith("+++ b/"):
            paths.add(_normalize_path(line.removeprefix("+++ b/")))
    return {path for path in paths if path and path != "/dev/null"}


def validate_patch_scope(patch: str, allowed: set[str]) -> None:
    if not patch.strip():
        return
    touched = extract_paths_from_unified_diff(patch)
    if not touched:
        msg = "patch does not contain recognizable file paths"
        raise CoderError(msg)
    normalized_allowed = {_normalize_path(path) for path in allowed}
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
        file_path=_normalize_path(file_path),
        steps=list(plan.steps),
        test_commands=list(plan.test_commands),
        acceptance_criteria=list(plan.acceptance_criteria),
    )


def _bundle_entry_for_file(
    context_bundle: ContextBundle,
    file_path: str,
) -> ContextFileEntry | None:
    normalized = _normalize_path(file_path)
    for entry in context_bundle.files:
        if _normalize_path(entry.path) == normalized:
            return entry
    return None


def build_coder_messages(
    issue: IssueContext,
    plan_slice: PlanSlice,
    file_content: str,
    bundle_entry: ContextFileEntry | None,
    *,
    correction: str | None = None,
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
) -> FilePatch:
    """Generate a normalized unified diff for one planned file."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise CoderError("LLM API key required for coder")

    normalized_path = _normalize_path(file_path)
    original = _read_repo_file(repo_path, normalized_path, settings)
    plan_slice = plan_slice_for_file(plan, normalized_path)
    bundle_entry = _bundle_entry_for_file(context_bundle, normalized_path)
    messages = build_coder_messages(issue, plan_slice, original, bundle_entry)

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

    file_patches: list[FilePatch] = []
    for path in plan.files:
        file_patch = generate_file_patch(
            repo_path,
            issue,
            plan,
            path,
            context_bundle,
            settings,
            logger=log,
        )
        file_patches.append(file_patch)

    combined = combine_file_patches(file_patches)
    log.info("Coder produced %d file patch(es)", len(file_patches))
    return CoderArtifact(
        issue_number=issue.number,
        repo=issue.repo,
        files=file_patches,
        combined_patch=combined,
    )


def write_coder_artifact(ctx: RunContext, artifact: CoderArtifact) -> Path:
    patch_path = ctx.artifact_dir / "proposed.patch"
    patch_path.write_text(artifact.combined_patch, encoding="utf-8")
    meta_path = ctx.artifact_dir / "coder_meta.json"
    meta_path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return patch_path
