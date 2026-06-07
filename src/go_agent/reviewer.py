"""Review agent — structured PR review with checklist and format/lint context."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.lint_runner import LintFinding, format_finding
from go_agent.llm_client import complete, llm_available
from go_agent.planner import FixPlan
from go_agent.run_context import RunContext
from go_agent.skills import format_skill_prompt

ReviewDecision = Literal["approve", "request_changes", "reject"]

_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/", re.MULTILINE)
_MAX_PATCH_CHARS = 8000
_MAX_OUTPUT_CHARS = 4000
_MAX_ISSUE_BODY_CHARS = 2000

REVIEWER_SYSTEM_PROMPT = """You are a senior Go open-source maintainer reviewing a proposed fix.

Return JSON only (no markdown fences) with exactly these keys:
- decision: one of "approve", "request_changes", "reject"
- comments: list of actionable review comments referencing concrete evidence
  (file paths, line numbers, test output, diff hunks, gofmt diffs, vet findings)
- checklist: object with boolean keys:
  - acceptance_criteria: issue acceptance criteria met
  - tests: tests adequately cover the change
  - api_breaks: no unintended public API breaks
  - style: code style and formatting acceptable
  - error_messages: error handling and messages follow repo conventions

Rules:
- decision must be "approve" only when ALL checklist values are true
- When gofmt or vet report issues, comments MUST cite the specific file (and line if available)
- Do not give generic style advice without evidence from the supplied context
- Prefer request_changes over reject for fixable issues"""


class ReviewError(RuntimeError):
    """Raised when review cannot be built or validated."""


class ReviewChecklist(BaseModel):
    acceptance_criteria: bool = False
    tests: bool = False
    api_breaks: bool = False
    style: bool = False
    error_messages: bool = False


class ReviewResult(BaseModel):
    """Structured review output written to review.json and graph state."""

    decision: ReviewDecision = "reject"
    comments: list[str] = Field(default_factory=list)
    checklist: ReviewChecklist = Field(default_factory=ReviewChecklist)

    @property
    def approved(self) -> bool:
        return self.decision == "approve"

    @field_validator("comments")
    @classmethod
    def validate_comments(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            msg = "comments must be non-empty"
            raise ValueError(msg)
        return cleaned

    @model_validator(mode="after")
    def validate_decision_matches_checklist(self) -> ReviewResult:
        if self.decision == "approve":
            checklist = self.checklist
            if not all(
                (
                    checklist.acceptance_criteria,
                    checklist.tests,
                    checklist.api_breaks,
                    checklist.style,
                    checklist.error_messages,
                )
            ):
                msg = 'decision "approve" requires all checklist values to be true'
                raise ValueError(msg)
        return self


class ReviewContext(BaseModel):
    issue: IssueContext
    plan: FixPlan
    patch_text: str
    changed_files: list[str]
    test_output: str
    test_passed: bool
    lint_output: str
    lint_passed: bool
    lint_findings: list[dict[str, Any]] = Field(default_factory=list)
    gofmt_diff: str = ""
    vet_output: str = ""


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n... [truncated]"


def parse_changed_files(patch_text: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    seen: set[str] = set()
    files: list[str] = []
    for match in _DIFF_FILE.finditer(patch_text):
        path = match.group(1)
        if path not in seen:
            seen.add(path)
            files.append(path)
    return files


def resolve_changed_files(plan: FixPlan, patch_text: str) -> list[str]:
    """Prefer plan files; fall back to parsing the patch."""
    if plan.files:
        return list(plan.files)
    return parse_changed_files(patch_text)


def run_gofmt_diff(repo_path: Path, files: list[str]) -> str:
    """Run gofmt -d on changed Go files; return diff output or empty if clean."""
    go_files = [path for path in files if path.endswith(".go")]
    if not go_files:
        return ""
    existing = [path for path in go_files if (repo_path / path).is_file()]
    if not existing:
        return ""
    try:
        completed = subprocess.run(
            ["gofmt", "-d", *existing],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    return _truncate(output)


def extract_vet_output(lint_result: dict[str, Any] | None) -> str:
    """Extract vet command output and structured findings from lint state."""
    if not lint_result:
        return ""
    parts: list[str] = []
    output = str(lint_result.get("output") or "").strip()
    if output:
        vet_lines = [
            line
            for line in output.splitlines()
            if "vet" in line.lower() or ".go:" in line
        ]
        if vet_lines:
            parts.append(_truncate("\n".join(vet_lines)))
        elif output:
            parts.append(_truncate(output))

    findings = lint_result.get("findings") or []
    if findings:
        formatted = [
            format_finding(LintFinding(**item))
            for item in findings[:20]
            if isinstance(item, dict)
        ]
        if formatted:
            parts.append("Findings:\n" + "\n".join(formatted))
    return "\n\n".join(parts).strip()


def build_review_context(
    issue: IssueContext,
    plan: FixPlan,
    *,
    repo_path: Path,
    patch_text: str,
    test_result: dict[str, Any] | None,
    lint_result: dict[str, Any] | None,
) -> ReviewContext:
    """Gather inputs for the review LLM prompt."""
    changed_files = resolve_changed_files(plan, patch_text)
    return ReviewContext(
        issue=issue,
        plan=plan,
        patch_text=_truncate(patch_text, _MAX_PATCH_CHARS),
        changed_files=changed_files,
        test_output=_truncate(str((test_result or {}).get("output") or "")),
        test_passed=bool((test_result or {}).get("passed")),
        lint_output=_truncate(str((lint_result or {}).get("output") or "")),
        lint_passed=bool((lint_result or {}).get("passed")) if lint_result else True,
        lint_findings=list((lint_result or {}).get("findings") or []),
        gofmt_diff=run_gofmt_diff(repo_path, changed_files),
        vet_output=extract_vet_output(lint_result),
    )


def build_review_messages(
    context: ReviewContext,
    *,
    correction: str | None = None,
) -> list[dict[str, str]]:
    """Build strong-tier LLM messages for the review checklist."""
    issue = context.issue
    plan = context.plan
    body = (issue.body or "")[:_MAX_ISSUE_BODY_CHARS]
    ac_text = "\n".join(f"- {item}" for item in plan.acceptance_criteria) or "(none)"
    steps_text = "\n".join(f"- {item}" for item in plan.steps) or "(none)"
    files_text = ", ".join(context.changed_files) or "(unknown)"
    skill_section = format_skill_prompt(issue.repo, max_chars=2000)

    gofmt_section = context.gofmt_diff or "(clean — no formatting diff)"
    vet_section = context.vet_output or context.lint_output or "(no vet/lint output)"

    user_parts = [
        f"Issue #{issue.number} ({issue.repo}): {issue.title}",
        f"Issue body excerpt:\n{body or '(empty)'}",
        f"Plan steps:\n{steps_text}",
        f"Acceptance criteria:\n{ac_text}",
        f"Changed files: {files_text}",
        f"Tests passed: {context.test_passed}",
        f"Test output:\n{context.test_output or '(empty)'}",
        f"Lint passed: {context.lint_passed}",
        f"Format check (gofmt -d on changed files):\n{gofmt_section}",
        f"Vet / lint output:\n{vet_section}",
        f"Patch diff:\n{context.patch_text or '(empty)'}",
    ]
    if skill_section:
        user_parts.append(skill_section)
    if correction:
        user_parts.append(correction)

    return [
        {"role": "system", "content": REVIEWER_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


def _parse_review_json(content: str) -> dict[str, Any]:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        msg = "LLM response did not contain a JSON object"
        raise ReviewError(msg)
    try:
        payload = json.loads(content[start : end + 1])
    except Exception as exc:
        msg = f"LLM response is not valid JSON: {exc}"
        raise ReviewError(msg) from exc
    if not isinstance(payload, dict):
        msg = "LLM review payload must be a JSON object"
        raise ReviewError(msg)
    return payload


def _validate_review(payload: dict[str, Any]) -> ReviewResult:
    try:
        return ReviewResult.model_validate(payload)
    except Exception as exc:
        msg = f"Review validation failed: {exc}"
        raise ReviewError(msg) from exc


def _request_review(messages: list[dict[str, str]], settings: Settings) -> ReviewResult:
    content = complete(messages, tier="strong", settings=settings)
    if not content:
        raise ReviewError("LLM completion failed")
    payload = _parse_review_json(content)
    return _validate_review(payload)


def build_review(
    issue: IssueContext,
    plan: FixPlan,
    *,
    repo_path: Path,
    patch_text: str,
    test_result: dict[str, Any] | None,
    lint_result: dict[str, Any] | None,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> ReviewResult:
    """Build and validate a structured review; raises ReviewError on failure."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise ReviewError("LLM API key required for reviewer")

    context = build_review_context(
        issue,
        plan,
        repo_path=repo_path,
        patch_text=patch_text,
        test_result=test_result,
        lint_result=lint_result,
    )
    messages = build_review_messages(context)
    try:
        review = _request_review(messages, settings)
        log.info("Review decision=%s (%d comment(s))", review.decision, len(review.comments))
        return review
    except ReviewError as first_error:
        log.warning("Reviewer first attempt failed: %s", first_error)
        retry_messages = build_review_messages(
            context,
            correction=(
                f"Previous output was invalid: {first_error}. "
                'Return valid JSON only with keys decision, comments, and checklist.'
            ),
        )
        try:
            review = _request_review(retry_messages, settings)
            log.info("Review built on retry decision=%s", review.decision)
            return review
        except ReviewError as retry_error:
            raise ReviewError(f"Reviewer failed after retry: {retry_error}") from retry_error


def write_review(ctx: RunContext, review: ReviewResult) -> Path:
    """Write review.json under the run artifact directory."""
    path = ctx.artifact_dir / "review.json"
    path.write_text(review.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
