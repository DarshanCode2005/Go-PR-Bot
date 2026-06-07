"""Fix agent — generate corrective patches from test/lint failures."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
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
)
from go_agent.config import Settings
from go_agent.context_builder import ContextBundle
from go_agent.github_issues import IssueContext
from go_agent.lint_runner import LintFinding, format_finding
from go_agent.llm_client import llm_available
from go_agent.planner import FixPlan
from go_agent.failure_parse import (
    GO_FILE_RE,
    parse_failing_packages,
    parse_failing_tests,
    parse_referenced_go_files,
    resolve_test_files,
)
from go_agent.run_context import RunContext
from go_agent.utils import normalize_file_path

FIXER_SYSTEM_PROMPT = """You are a Go fix agent correcting code after test or lint failures.

Given the plan, current file content, and validation errors, emit the smallest correct fix.
Output either:
1) SEARCH/REPLACE blocks only (preferred):
--- SEARCH
<exact lines copied from the file>
+++ REPLACE
<replacement lines>
2) OR a unified diff for the target file only.

Rules:
- Edit ONLY the target file named in the user message (one file per response)
- You may receive multiple files across separate fix waves; do not edit files not listed in "Allowed files for this fix iteration"
- Fix the reported errors without unrelated refactors
- SEARCH text must match the file exactly (copy indentation character-for-character)
- Do NOT wrap output in markdown code fences (no ``` lines)"""


class FixError(RuntimeError):
    """Raised when fix patch generation fails."""


class FixContext(BaseModel):
    iteration: int
    max_iterations: int
    failure_source: Literal["test", "lint", "review"]
    test_output: str = ""
    lint_output: str = ""
    lint_findings: list[dict[str, Any]] = Field(default_factory=list)
    review_comments: list[str] = Field(default_factory=list)
    review_round: int = 0


class FixMeta(BaseModel):
    iteration: int
    max_iterations: int
    failure_source: Literal["test", "lint", "review"]
    error_summary: str
    files: list[str] = Field(default_factory=list)
    review_round: int | None = None


class PlanExpansion(BaseModel):
    iteration: int
    original_files: list[str]
    added_files: list[str]
    failing_tests: list[str] = Field(default_factory=list)
    reason: str


@dataclass(frozen=True)
class FixScopeExpansion:
    target_files: list[str]
    added_files: list[str]
    failing_tests: list[str]
    reason: str


@dataclass(frozen=True)
class CorrectivePatchResult:
    artifact: CoderArtifact
    expansion: FixScopeExpansion


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


def build_review_fix_context(
    state: dict[str, Any],
    *,
    max_review_rounds: int,
    max_iterations: int,
) -> FixContext:
    """Build fix context from reviewer request_changes feedback."""
    review = state.get("review") or {}
    comments = [str(item) for item in (review.get("comments") or []) if str(item).strip()]
    review_round = state.get("review_round", 0) + 1
    return FixContext(
        iteration=state.get("iteration", 0) + 1,
        max_iterations=max_iterations,
        failure_source="review",
        review_comments=comments,
        review_round=review_round,
    )


def _failure_summary(
    fix_context: FixContext,
    *,
    scope: FixScopeExpansion | None = None,
) -> str:
    parts: list[str] = [
        f"Failure source: {fix_context.failure_source}",
        f"Fix iteration: {fix_context.iteration}/{fix_context.max_iterations}",
    ]
    if scope is not None:
        parts.append(f"Allowed files for this fix iteration: {', '.join(scope.target_files)}")
        if scope.failing_tests:
            parts.append(f"Failing tests: {', '.join(scope.failing_tests)}")
    if fix_context.failure_source == "review":
        parts.append(f"Review round: {fix_context.review_round}")
        if fix_context.review_comments:
            parts.append("Review feedback:\n" + "\n".join(f"- {item}" for item in fix_context.review_comments))
        return "\n\n".join(parts)
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


def _collect_mentioned_files(fix_context: FixContext) -> set[str]:
    mentioned: set[str] = set()
    for item in fix_context.lint_findings:
        raw = item.get("file")
        if raw:
            mentioned.add(normalize_file_path(str(raw)))
    combined = fix_context.test_output + "\n" + fix_context.lint_output
    for path in parse_referenced_go_files(combined):
        mentioned.add(path)
    return mentioned


def _prioritize_plan_files(plan: FixPlan, fix_context: FixContext) -> list[str]:
    mentioned = _collect_mentioned_files(fix_context)

    ordered: list[str] = []
    for path in plan.files:
        if normalize_file_path(path) in mentioned:
            ordered.append(path)
    for path in plan.files:
        if path not in ordered:
            ordered.append(path)
    return ordered or list(plan.files)


def expand_fix_files(
    plan: FixPlan,
    fix_context: FixContext,
    repo_path: Path,
    settings: Settings,
    *,
    max_extra: int = 2,
    logger: logging.Logger | None = None,
) -> FixScopeExpansion:
    """Expand fix scope with files mentioned in failures that exist on disk."""
    log = logger or logging.getLogger("go_agent")
    ordered = _prioritize_plan_files(plan, fix_context)
    mentioned = _collect_mentioned_files(fix_context)
    plan_normalized = {normalize_file_path(path) for path in plan.files}

    failing_tests: list[str] = []
    if fix_context.failure_source == "test":
        failing_tests = parse_failing_tests(fix_context.test_output)

    resolved_test_files: list[str] = []
    if failing_tests:
        resolved_test_files = resolve_test_files(
            repo_path,
            failing_tests,
            settings,
            logger=log,
        )

    extra_candidates: list[str] = []
    seen_extra: set[str] = set()

    def consider(path: str) -> None:
        norm = normalize_file_path(path)
        if norm in plan_normalized or norm in seen_extra:
            return
        if not (repo_path / norm).is_file():
            return
        seen_extra.add(norm)
        extra_candidates.append(norm)

    for path in resolved_test_files:
        consider(path)
    for line in fix_context.test_output.splitlines():
        upper = line.upper()
        if "FAIL:" in upper or upper.strip().startswith("--- FAIL"):
            for match in GO_FILE_RE.finditer(line):
                consider(match.group(1))
    for path in sorted(mentioned):
        if path.endswith("_test.go"):
            consider(path)
    for path in sorted(mentioned):
        consider(path)

    cap = len(plan.files) + max_extra
    result = list(ordered)
    for path in extra_candidates:
        if len(result) >= cap:
            break
        result.append(path)

    added = [path for path in result if normalize_file_path(path) not in plan_normalized]
    reason_parts: list[str] = []
    if failing_tests:
        reason_parts.append(
            f"Failing tests: {', '.join(failing_tests)}"
        )
    if resolved_test_files:
        reason_parts.append(
            f"Resolved test files: {', '.join(resolved_test_files)}"
        )
    if added:
        reason_parts.append(f"Added files: {', '.join(added)}")
    reason = "; ".join(reason_parts) if reason_parts else "No scope expansion"

    return FixScopeExpansion(
        target_files=result,
        added_files=added,
        failing_tests=failing_tests,
        reason=reason,
    )


def build_corrective_patch(
    repo_path: Path,
    issue: IssueContext,
    plan: FixPlan,
    context_bundle: ContextBundle,
    fix_context: FixContext,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> CorrectivePatchResult:
    """Generate corrective patches for planned files using validation failure context."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise FixError("LLM API key required for fix agent")

    expansion = expand_fix_files(plan, fix_context, repo_path, settings, logger=log)
    if expansion.added_files:
        log.info(
            "fix scope expanded: %s",
            ", ".join(f"+{path}" for path in expansion.added_files),
        )
    if not expansion.target_files:
        raise FixError("plan.files is empty; nothing to fix")

    failure_text = _failure_summary(fix_context, scope=expansion)
    narrowed_plan = plan.model_copy(update={"files": expansion.target_files})
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

    file_patches = [completed[path] for path in expansion.target_files if path in completed]
    combined = combine_file_patches(file_patches)
    if not combined.strip():
        raise FixError("fix agent produced empty patch")

    artifact = CoderArtifact(
        issue_number=issue.number,
        repo=issue.repo,
        files=file_patches,
        combined_patch=combined,
        execution_waves=waves,
    )
    return CorrectivePatchResult(artifact=artifact, expansion=expansion)


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
        slice_file_for_coder_prompt,
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
    prompt_content, file_excerpt = slice_file_for_coder_prompt(
        file_content,
        plan_slice,
        settings.coder_max_file_chars,
    )
    messages = build_coder_messages(
        issue,
        plan_slice,
        prompt_content,
        bundle_entry,
        correction=failure_text,
        dependency_context=dependency_context,
        file_excerpt=file_excerpt,
    )
    messages[0]["content"] = FIXER_SYSTEM_PROMPT

    from go_agent.llm_client import complete
    from go_agent.coder import normalize_llm_patch

    try:
        content = complete(messages, tier="fast", settings=settings)
        if not content:
            raise FixError(f"LLM completion failed for {file_path}")
        patch = normalize_llm_patch(file_path, file_content, content, plan)
        logger.info("Fix patch generated for %s (%s)", file_path, patch.format)
        return patch
    except CoderError as first_error:
        logger.warning("Fix first attempt failed for %s: %s", file_path, first_error)
        retry_messages = list(messages)
        retry_messages.append(
            {
                "role": "user",
                "content": (
                    f"Previous output was invalid: {first_error}. "
                    "Return SEARCH/REPLACE blocks or a unified diff for this file only."
                ),
            }
        )
        content = complete(retry_messages, tier="fast", settings=settings)
        if not content:
            raise FixError(f"Fix failed after retry for {file_path}") from first_error
        try:
            patch = normalize_llm_patch(file_path, file_content, content, plan)
            logger.info("Fix patch generated for %s on retry (%s)", file_path, patch.format)
            return patch
        except CoderError as retry_error:
            raise FixError(f"fix agent failed for {file_path}: {retry_error}") from retry_error


def write_fix_meta(ctx: RunContext, meta: FixMeta) -> Path:
    """Write fix_meta.json under the run artifact directory."""
    path = ctx.artifact_dir / "fix_meta.json"
    path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def write_plan_expansion(ctx: RunContext, record: PlanExpansion) -> Path:
    """Write plan_expansion.json when fix scope grows beyond the original plan."""
    path = ctx.artifact_dir / "plan_expansion.json"
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "CorrectivePatchResult",
    "FixContext",
    "FixError",
    "FixMeta",
    "FixScopeExpansion",
    "PlanExpansion",
    "build_corrective_patch",
    "build_failure_context",
    "build_review_fix_context",
    "expand_fix_files",
    "parse_failing_packages",
    "parse_failing_tests",
    "parse_referenced_go_files",
    "resolve_test_files",
    "write_fix_meta",
    "write_plan_expansion",
]
