"""Integrator — sequential patch apply with LLM conflict merge."""

from __future__ import annotations

import logging
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.coder import CoderError, FilePatch, normalize_llm_patch
from go_agent.config import Settings
from go_agent.llm_client import complete, llm_available
from go_agent.patches import PatchApplyError, apply_unified_patch, export_changes_patch
from go_agent.planner import FixPlan
from go_agent.run_context import RunContext
from go_agent.git_util import run_git
from go_agent.utils import normalize_file_path

MERGE_SYSTEM_PROMPT = """You are merging overlapping code edits to a single file.

Multiple patches could not all be applied cleanly. Combine their intent into one correct
change relative to the original file content.

Output either:
1) SEARCH/REPLACE blocks only (preferred):
--- SEARCH
<exact lines from the original file>
+++ REPLACE
<replacement lines>
2) OR a unified diff for this file only.

Rules:
- Edit ONLY the named file
- Preserve the intent of every conflicting change
- Make the smallest correct merged change"""


class IntegratorError(RuntimeError):
    """Raised when patch integration or conflict merge fails."""


class ConflictResolution(BaseModel):
    path: str
    patch_count: int
    merge_format: Literal["search_replace", "unified_diff"]


class IntegratorResult(BaseModel):
    resolved_patch: str
    conflicts: list[ConflictResolution] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)


def _snapshot_file(repo_path: Path, path: str) -> str:
    full_path = repo_path / path
    if not full_path.is_file():
        msg = f"integration base file not found: {path}"
        raise IntegratorError(msg)
    return full_path.read_text(encoding="utf-8")


def _reset_file(repo_path: Path, path: str, content: str) -> None:
    full_path = repo_path / path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")


def try_apply_patch(repo_path: Path, patch: str) -> None:
    """Validate and apply a unified diff; raise IntegratorError on failure."""
    if not patch.strip():
        return
    try:
        apply_unified_patch(repo_path, patch)
    except PatchApplyError as exc:
        raise IntegratorError(str(exc)) from exc


def _patch_description(index: int, file_patch: FilePatch) -> str:
    if file_patch.search_replace_raw:
        return f"### Patch {index} (search_replace)\n{file_patch.search_replace_raw.strip()}"
    return f"### Patch {index} (unified_diff)\n{file_patch.patch.strip()}"


def build_merge_messages(
    path: str,
    base_content: str,
    patches: list[FilePatch],
    *,
    current_content: str | None = None,
    correction: str | None = None,
) -> list[dict[str, str]]:
    hunk_parts = [_patch_description(index + 1, item) for index, item in enumerate(patches)]
    user_parts = [
        f"Target file: {path}",
        f"Original file content:\n{base_content}",
        "Conflicting patches:\n" + "\n\n".join(hunk_parts),
    ]
    if current_content is not None and current_content != base_content:
        user_parts.append(f"Current file content after partial apply:\n{current_content}")
    if correction:
        user_parts.append(correction)
    return [
        {"role": "system", "content": MERGE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def merge_patches_with_llm(
    path: str,
    base_content: str,
    patches: list[FilePatch],
    plan: FixPlan,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> FilePatch:
    """Merge overlapping patches for one file with a single LLM call."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise IntegratorError("LLM API key required for conflict merge")

    messages = build_merge_messages(path, base_content, patches)
    max_attempts = settings.integrator_max_merge_retries + 1
    last_error: IntegratorError | None = None

    for attempt in range(max_attempts):
        try:
            content = complete(messages, tier="fast", settings=settings)
            if not content:
                raise IntegratorError("LLM merge completion failed")
            merged = normalize_llm_patch(path, base_content, content, plan)
            log.info("Merged %d overlapping patch(es) for %s", len(patches), path)
            return merged
        except (IntegratorError, CoderError) as exc:
            last_error = IntegratorError(str(exc))
            log.warning("Integrator merge attempt %d failed for %s: %s", attempt + 1, path, exc)
            messages = build_merge_messages(
                path,
                base_content,
                patches,
                correction=(
                    f"Previous merge output was invalid: {exc}. "
                    "Return SEARCH/REPLACE blocks or a unified diff for this file only."
                ),
            )

    msg = f"Integrator merge failed for {path} after retry"
    raise IntegratorError(msg) from last_error


def _order_file_patches(file_patches: list[FilePatch], plan: FixPlan) -> list[FilePatch]:
    buckets: dict[str, list[FilePatch]] = defaultdict(list)
    for item in file_patches:
        buckets[normalize_file_path(item.path)].append(item)
    ordered: list[FilePatch] = []
    seen: set[str] = set()
    for path in plan.files:
        key = normalize_file_path(path)
        if key in buckets:
            ordered.extend(buckets[key])
            seen.add(key)
    for item in file_patches:
        key = normalize_file_path(item.path)
        if key not in seen:
            ordered.append(item)
            seen.add(key)
    return ordered


def _patches_for_path(ordered: list[FilePatch], path: str) -> list[FilePatch]:
    normalized = normalize_file_path(path)
    return [item for item in ordered if normalize_file_path(item.path) == normalized]


def integrate_file_patches(
    repo_path: Path,
    file_patches: list[FilePatch],
    plan: FixPlan,
    base_sha: str,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> IntegratorResult:
    """Apply patches in plan order; merge overlapping hunks via LLM when needed."""
    log = logger or logging.getLogger("go_agent")
    if not file_patches:
        raise IntegratorError("no file patches to integrate")

    run_git(["reset", "--hard", base_sha], cwd=repo_path)
    ordered = _order_file_patches(file_patches, plan)
    base_by_path: dict[str, str] = {}
    merged_paths: set[str] = set()
    conflicts: list[ConflictResolution] = []
    files_touched: list[str] = []

    index = 0
    while index < len(ordered):
        file_patch = ordered[index]
        path = file_patch.path
        path_key = normalize_file_path(path)

        if path_key in merged_paths:
            index += 1
            continue

        if path_key not in base_by_path:
            base_by_path[path_key] = _snapshot_file(repo_path, path)

        try:
            try_apply_patch(repo_path, file_patch.patch)
            if path not in files_touched:
                files_touched.append(path)
            index += 1
            continue
        except IntegratorError as first_error:
            log.warning("Patch apply conflict for %s: %s", path, first_error)
            conflicting = _patches_for_path(ordered, path)
            merged = merge_patches_with_llm(
                path,
                base_by_path[path_key],
                conflicting,
                plan,
                settings,
                logger=log,
            )
            _reset_file(repo_path, path, base_by_path[path_key])
            try_apply_patch(repo_path, merged.patch)
            conflicts.append(
                ConflictResolution(
                    path=path,
                    patch_count=len(conflicting),
                    merge_format=merged.format,
                )
            )
            merged_paths.add(path_key)
            if path not in files_touched:
                files_touched.append(path)
            while index < len(ordered) and normalize_file_path(ordered[index].path) == path_key:
                index += 1

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".patch",
        delete=False,
        encoding="utf-8",
    ) as handle:
        diff_dest = Path(handle.name)

    try:
        try:
            export_changes_patch(repo_path, base_sha, diff_dest)
            resolved_patch = diff_dest.read_text(encoding="utf-8")
        except PatchApplyError as exc:
            raise IntegratorError(str(exc)) from exc
    finally:
        diff_dest.unlink(missing_ok=True)
        run_git(["reset", "--hard", base_sha], cwd=repo_path)
    log.info(
        "Integrator resolved %d file(s); %d conflict merge(s)",
        len(files_touched),
        len(conflicts),
    )
    return IntegratorResult(
        resolved_patch=resolved_patch,
        conflicts=conflicts,
        files_touched=files_touched,
    )


def write_integrator_artifact(ctx: RunContext, result: IntegratorResult) -> Path:
    resolved_path = ctx.artifact_dir / "resolved.patch"
    resolved_path.write_text(result.resolved_patch, encoding="utf-8")
    meta_path = ctx.artifact_dir / "integrator_meta.json"
    meta_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return meta_path
