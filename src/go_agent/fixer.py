"""Fix agent — generate corrective patches from test/lint failures."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from go_agent.coder import (
    CoderArtifact,
    CoderError,
    FilePatch,
    combine_file_patches,
    plan_slice_for_file,
    schedule_coder_waves,
    write_coder_artifact,
)
from go_agent.config import Settings
from go_agent.context_builder import ContextBundle
from go_agent.github_issues import IssueContext
from go_agent.lint_runner import LintFinding, format_finding
from go_agent.llm_client import llm_available
from go_agent.planner import FixPlan
from go_agent.run_context import RunContext
from go_agent.utils import normalize_file_path

_GO_FILE = re.compile(r"(?:\./)?([\w./-]+\.go)")

FIXER_SYSTEM_PROMPT = """You are a Go fix agent correcting code after test or lint failures.

Given the plan, current file content, and validation errors, emit the smallest correct fix.
Output either SEARCH/REPLACE blocks or a unified diff for the target file only.

Rules:
- Edit ONLY the file named in the user message
- Fix the reported errors without unrelated refactors
- SEARCH text must match the file exactly"""


class FixError(RuntimeError):
    """Raised when fix patch generation fails."""


class FixContext(BaseModel):
    iteration: int
    max_iterations: int
    failure_source: Literal["test", "lint"]
    test_output: str = ""
    lint_output: str = ""
    lint_findings: list[dict[str, Any]] = Field(default_factory=list)


class FixMeta(BaseModel):
    iteration: int
    max_iterations: int
    failure_source: Literal["test", "lint"]
    error_summary: str
    files: list[str] = Field(default_factory=list)


def build_failure_context(
    state: dict[str, Any],
    *,
    max_iterations: int,
) -> FixContext:
    """Build fix context from graph state after a test or lint failure."""
    last_node = state.get("last_node", "")
    failure_source: Literal["test", "lint"] = "lint" if last_node == "lint" else "test"
    test_result = state.get("test_result") or {}
    lint_result = state.get("lint_result") or {}
    return FixContext(
        iteration=state.get("iteration", 0) + 1,
        max_iterations=max_iterations,
        failure_source=failure_source,
        test_output=str(test_result.get("output", ""))[:8000],
        lint_output=str(lint_result.get("output", ""))[:8000],
        lint_findings=list(lint_result.get("findings") or [])[:20],
    )


def _failure_summary(fix_context: FixContext) -> str:
    parts: list[str] = [
        f"Failure source: {fix_context.failure_source}",
        f"Fix iteration: {fix_context.iteration}/{fix_context.max_iterations}",
    ]
    if fix_context.failure_source == "test" and fix_context.test_output:
        parts.append(f"Test output:\n{fix_context.test_output}")
    if fix_context.lint_output:
        parts.append(f"Lint output:\n{fix_context.lint_output}")
    if fix_context.lint_findings:
        lines = []
        for item in fix_context.lint_findings[:10]:
            finding = LintFinding(**item)
            lines.append(format_finding(finding))
        parts.append("Lint findings:\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _prioritize_plan_files(plan: FixPlan, fix_context: FixContext) -> list[str]:
    mentioned: set[str] = set()
    for item in fix_context.lint_findings:
        raw = item.get("file")
        if raw:
            mentioned.add(normalize_file_path(str(raw)))
    combined = fix_context.test_output + "\n" + fix_context.lint_output
    for match in _GO_FILE.finditer(combined):
        mentioned.add(normalize_file_path(match.group(1)))

    ordered: list[str] = []
    for path in plan.files:
        if normalize_file_path(path) in mentioned:
            ordered.append(path)
    for path in plan.files:
        if path not in ordered:
            ordered.append(path)
    return ordered or list(plan.files)


def build_corrective_patch(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    context_bundle: ContextBundle,
    fix_context: FixContext,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> CoderArtifact:
    """Generate corrective patches for planned files using validation failure context."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise FixError("LLM API key required for fix agent")

    failure_text = _failure_summary(fix_context)
    target_files = _prioritize_plan_files(plan, fix_context)
    if not target_files:
        raise FixError("plan.files is empty; nothing to fix")

    narrowed_plan = plan.model_copy(update={"files": target_files})
    waves = schedule_coder_waves(narrowed_plan)
    completed: dict[str, FilePatch] = {}

    for wave_index, wave in enumerate(waves):
        log.info("Fix wave %d: %d files", wave_index, len(wave))
        if len(wave) == 1:
            path = wave[0]
            completed[path] = _generate_fix_file_patch(
                repo_path,
                issue,
                narrowed_plan,
                path,
                context_bundle,
                settings,
                failure_text,
                dict(completed),
                log,
            )
            continue

        max_workers = max(1, min(settings.coder_max_workers, len(wave)))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    _generate_fix_file_patch,
                    repo_path,
                    issue,
                    narrowed_plan,
                    path,
                    context_bundle,
                    settings,
                    failure_text,
                    dict(completed),
                    log,
                ): path
                for path in wave
            }
            try:
                for future in as_completed(futures):
                    path = futures[future]
                    completed[path] = future.result()
            except Exception as exc:
                if isinstance(exc, FixError):
                    raise
                raise FixError(str(exc)) from exc

    file_patches = [completed[path] for path in target_files if path in completed]
    combined = combine_file_patches(file_patches)
    if not combined.strip():
        raise FixError("fix agent produced empty patch")

    return CoderArtifact(
        issue_number=issue.number,
        repo=issue.repo,
        files=file_patches,
        combined_patch=combined,
        execution_waves=waves,
    )


def _generate_fix_file_patch(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    file_path: str,
    context_bundle: ContextBundle,
    settings: Settings,
    failure_text: str,
    completed: dict[str, FilePatch],
    logger: logging.Logger,
) -> FilePatch:
    from go_agent.coder import (
        _dependency_context_for_file,
        _read_file_for_coding,
        build_coder_messages,
    )

    file_content = _read_file_for_coding(repo_path, file_path, settings)
    dependency_context = _dependency_context_for_file(
        repo_path,
        file_path,
        completed,
        plan,
        settings,
    )
    plan_slice = plan_slice_for_file(plan, file_path)
    from go_agent.coder import _bundle_entry_for_file

    bundle_entry = _bundle_entry_for_file(context_bundle, file_path)
    messages = build_coder_messages(
        issue,
        plan_slice,
        file_content,
        bundle_entry,
        correction=failure_text,
        dependency_context=dependency_context,
    )
    messages[0]["content"] = FIXER_SYSTEM_PROMPT

    try:
        from go_agent.llm_client import complete
        from go_agent.coder import normalize_llm_patch

        content = complete(messages, tier="fast", settings=settings)
        if not content:
            raise FixError(f"LLM completion failed for {file_path}")
        patch = normalize_llm_patch(file_path, file_content, content, plan)
        logger.info("Fix patch generated for %s (%s)", file_path, patch.format)
        return patch
    except CoderError as exc:
        raise FixError(f"fix agent failed for {file_path}: {exc}") from exc


def write_fix_meta(ctx: RunContext, meta: FixMeta) -> Path:
    """Write fix_meta.json under the run artifact directory."""
    path = ctx.artifact_dir / "fix_meta.json"
    path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "FixContext",
    "FixError",
    "FixMeta",
    "build_corrective_patch",
    "build_failure_context",
    "write_coder_artifact",
    "write_fix_meta",
]
