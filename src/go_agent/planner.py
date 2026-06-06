"""Planner agent — structured fix plan from issue and context bundle."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from go_agent.config import Settings
from go_agent.context_builder import ContextBundle
from go_agent.github_issues import IssueContext
from go_agent.llm_client import complete, llm_available
from go_agent.run_context import RunContext
from go_agent.skills import load_skill_text
from go_agent.utils import normalize_file_path

PLANNER_SYSTEM_PROMPT = """You are a senior Go open-source maintainer planning a minimal fix for a GitHub issue.

Return JSON only (no markdown fences) with exactly these keys:
- files: list of repository-relative file paths to modify (prefer .go files)
- steps: ordered list of concrete implementation steps
- test_commands: list of shell commands to verify the fix (must include go test)
- acceptance_criteria: list of verifiable conditions for done
- file_dependencies: optional map of file path -> list of paths that file depends on
  (e.g. {"pkg/bar.go": ["pkg/foo.go"]} when bar must be coded after foo). Omit or {} if independent.

Be specific to the issue and supplied code context. Prefer the smallest correct change."""

_MAX_ISSUE_BODY_CHARS = 3000
_MAX_BUNDLE_ENTRY_CHARS = 2000
_DEFAULT_MAX_CONTEXT_CHARS = 12000


class PlanError(RuntimeError):
    """Raised when fix plan cannot be built or validated."""


def _detect_dependency_cycle(files: list[str], dependencies: dict[str, list[str]]) -> None:
    graph: dict[str, list[str]] = {path: list(dependencies.get(path, [])) for path in files}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            msg = f"file_dependencies contains a cycle involving {node}"
            raise ValueError(msg)
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for path in files:
        visit(path)


class FixPlan(BaseModel):
    issue_number: int
    repo: str
    files: list[str]
    steps: list[str]
    test_commands: list[str]
    acceptance_criteria: list[str]
    file_dependencies: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            msg = "files must be non-empty"
            raise ValueError(msg)
        deduped: list[str] = []
        seen: set[str] = set()
        for path in cleaned:
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    @field_validator("steps", "acceptance_criteria")
    @classmethod
    def validate_non_empty_strings(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            msg = "list must contain at least one non-empty string"
            raise ValueError(msg)
        return cleaned

    @field_validator("test_commands")
    @classmethod
    def validate_test_commands(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            msg = "test_commands must be non-empty"
            raise ValueError(msg)
        if not any("go test" in command for command in cleaned):
            msg = "test_commands must include at least one go test command"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def validate_file_dependencies(self) -> FixPlan:
        canonical = {normalize_file_path(path): path for path in self.files}
        normalized_deps: dict[str, list[str]] = {}

        for raw_key, raw_values in self.file_dependencies.items():
            key = normalize_file_path(raw_key)
            if key not in canonical:
                msg = f"file_dependencies key {raw_key!r} is not listed in files"
                raise ValueError(msg)
            deps: list[str] = []
            for raw_dep in raw_values:
                dep = normalize_file_path(raw_dep)
                if dep not in canonical:
                    msg = f"file_dependencies for {raw_key!r} references unknown file {raw_dep!r}"
                    raise ValueError(msg)
                if dep == key:
                    msg = f"file {raw_key!r} cannot depend on itself"
                    raise ValueError(msg)
                canonical_dep = canonical[dep]
                if canonical_dep not in deps:
                    deps.append(canonical_dep)
            normalized_deps[canonical[key]] = deps

        ordered_files = [canonical[normalize_file_path(path)] for path in self.files]
        dep_lookup = {
            normalize_file_path(path): [
                normalize_file_path(item) for item in normalized_deps.get(path, [])
            ]
            for path in ordered_files
        }
        _detect_dependency_cycle(
            [normalize_file_path(path) for path in ordered_files],
            dep_lookup,
        )
        object.__setattr__(self, "file_dependencies", normalized_deps)
        return self


def _bundle_excerpt(context_bundle: ContextBundle, *, max_chars: int) -> str:
    parts: list[str] = []
    used = 0
    for entry in context_bundle.files:
        header = (
            f"### {entry.path} ({entry.content_tier}, {entry.rationale})\n"
        )
        content = entry.content[:_MAX_BUNDLE_ENTRY_CHARS]
        block = f"{header}{content}\n"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def build_planner_messages(
    issue: IssueContext,
    context_bundle: ContextBundle,
    scope_hints: list[str],
    *,
    max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS,
    correction: str | None = None,
) -> list[dict[str, str]]:
    """Build system + user messages for the planner LLM call."""
    skill_text = load_skill_text(issue.repo)
    hints_text = ", ".join(scope_hints[:30]) or "(none)"
    bundle_text = _bundle_excerpt(context_bundle, max_chars=max_context_chars)
    body = issue.body[:_MAX_ISSUE_BODY_CHARS].strip()

    user_parts = [
        f"Issue #{issue.number} in {issue.repo}",
        f"Title: {issue.title}",
        f"Body:\n{body or '(empty)'}",
        f"Scope hints: {hints_text}",
        f"Context bundle ({len(context_bundle.files)} files):\n{bundle_text or '(empty)'}",
    ]
    if skill_text:
        user_parts.append(f"Repo skill notes:\n{skill_text}")
    if correction:
        user_parts.append(correction)

    return [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _parse_plan_json(content: str) -> dict:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        msg = "LLM response did not contain a JSON object"
        raise PlanError(msg)
    try:
        import json

        payload = json.loads(content[start : end + 1])
    except Exception as exc:
        msg = f"LLM response is not valid JSON: {exc}"
        raise PlanError(msg) from exc
    if not isinstance(payload, dict):
        msg = "LLM plan payload must be a JSON object"
        raise PlanError(msg)
    return payload


def _validate_fix_plan(payload: dict, issue: IssueContext) -> FixPlan:
    merged = {
        **payload,
        "issue_number": issue.number,
        "repo": issue.repo,
    }
    try:
        return FixPlan.model_validate(merged)
    except Exception as exc:
        msg = f"Plan validation failed: {exc}"
        raise PlanError(msg) from exc


def _request_plan(
    issue: IssueContext,
    messages: list[dict[str, str]],
    settings: Settings,
) -> FixPlan:
    content = complete(messages, tier="strong", settings=settings)
    if not content:
        raise PlanError("LLM completion failed")
    payload = _parse_plan_json(content)
    return _validate_fix_plan(payload, issue)


def build_fix_plan(
    issue: IssueContext,
    context_bundle: ContextBundle,
    scope_hints: list[str],
    settings: Settings,
    logger: logging.Logger | None = None,
) -> FixPlan:
    """Build and validate a structured fix plan; raises PlanError on failure."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise PlanError("LLM API key required for planner")

    messages = build_planner_messages(issue, context_bundle, scope_hints)
    try:
        plan = _request_plan(issue, messages, settings)
        log.info(
            "Fix plan built: %d files, %d steps",
            len(plan.files),
            len(plan.steps),
        )
        return plan
    except PlanError as first_error:
        log.warning("Planner first attempt failed: %s", first_error)
        retry_messages = build_planner_messages(
            issue,
            context_bundle,
            scope_hints,
            correction=(
                f"Previous output was invalid: {first_error}. "
                "Return valid JSON only with keys files, steps, test_commands, "
                "acceptance_criteria, and optional file_dependencies."
            ),
        )
        try:
            plan = _request_plan(issue, retry_messages, settings)
            log.info("Fix plan built on retry")
            return plan
        except PlanError as retry_error:
            raise PlanError(f"Planner failed after retry: {retry_error}") from retry_error


def write_plan(ctx: RunContext, plan: FixPlan) -> Path:
    path = ctx.artifact_dir / "plan.json"
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
