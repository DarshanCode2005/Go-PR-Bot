"""Generate PR title and body drafts from issue context and optional patch."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.patches import format_commit_message
from go_agent.run_context import RunContext

_DIFF_FILE = re.compile(r"^diff --git a/(.+?) b/", re.MULTILINE)
_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HEADING = re.compile(r"^#+\s*", re.MULTILINE)
_PACKAGE_HINT = re.compile(r"^(?:\./)?[\w./-]+/?$")


class PRDraft(BaseModel):
    title: str
    problem: str
    solution: str
    test_plan: str
    issue_number: int
    repo: str


def _first_paragraph(body: str, *, max_length: int = 500) -> str:
    text = _CODE_FENCE.sub("", body).strip()
    text = _HEADING.sub("", text).strip()
    for block in re.split(r"\n\s*\n", text):
        cleaned = block.strip()
        if cleaned:
            if len(cleaned) > max_length:
                return cleaned[:max_length].rstrip() + "..."
            return cleaned
    return ""


def _parse_changed_files(patch_text: str) -> list[str]:
    seen: set[str] = set()
    files: list[str] = []
    for match in _DIFF_FILE.finditer(patch_text):
        path = match.group(1)
        if path not in seen:
            seen.add(path)
            files.append(path)
    return files


def _scoped_test_command(scope_hints: list[str] | None) -> str | None:
    if not scope_hints:
        return None
    for hint in scope_hints:
        if hint.endswith(".go"):
            package = hint.rsplit("/", 1)[0]
            if package and package != hint:
                return f"go test ./{package}/... -count=1"
        if _PACKAGE_HINT.match(hint) and "/" in hint:
            return f"go test ./{hint}/... -count=1"
    return None


def _build_test_plan(scope_hints: list[str] | None) -> str:
    lines = ["- [ ] go test ./... -count=1"]
    scoped = _scoped_test_command(scope_hints)
    if scoped and scoped not in lines[0]:
        lines.append(f"- [ ] {scoped}")
    return "\n".join(lines)


def build_pr_template(
    issue: IssueContext,
    *,
    scope_hints: list[str] | None = None,
    patch_text: str | None = None,
    commit_message: str | None = None,
) -> PRDraft:
    """Build a PR draft from issue metadata without calling an LLM."""
    title = commit_message or format_commit_message(issue.title[:50], issue.number)
    body_excerpt = _first_paragraph(issue.body)
    problem_parts = [issue.title]
    if body_excerpt:
        problem_parts.append(body_excerpt)
    problem = "\n\n".join(problem_parts)

    if patch_text and patch_text.strip():
        changed = _parse_changed_files(patch_text)
        if changed:
            file_lines = "\n".join(f"- `{path}`" for path in changed)
            solution = f"Updates the following files:\n\n{file_lines}"
        else:
            solution = "See `changes.patch` in run artifacts for the full diff."
    elif scope_hints:
        focus = ", ".join(scope_hints[:8])
        solution = f"Planned focus based on issue scope hints: {focus}."
    else:
        solution = "Implementation pending; agent pipeline not yet complete."

    return PRDraft(
        title=title,
        problem=problem,
        solution=solution,
        test_plan=_build_test_plan(scope_hints),
        issue_number=issue.number,
        repo=issue.repo,
    )


def enrich_pr_llm(
    draft: PRDraft,
    issue: IssueContext,
    settings: Settings,
    *,
    patch_text: str | None = None,
    scope_hints: list[str] | None = None,
) -> PRDraft:
    """Optionally refine PR sections via LLM; return template draft on failure."""
    if not settings.openai_api_key and not settings.anthropic_api_key:
        return draft

    try:
        import litellm
    except ImportError:
        return draft

    patch_excerpt = (patch_text or "")[:2000]
    hints_text = ", ".join(scope_hints or [])[:500]
    prompt = (
        "Improve this GitHub pull request draft for a Go OSS fix. "
        "Return JSON only with keys: title, problem, solution, test_plan. "
        "test_plan must be markdown checklist lines starting with '- [ ]'. "
        "Include exact go test commands.\n\n"
        f"Issue title: {issue.title}\n\nIssue body:\n{issue.body[:2000]}\n\n"
        f"Scope hints: {hints_text}\n\nPatch excerpt:\n{patch_excerpt}\n\n"
        f"Current draft:\n"
        f"title: {draft.title}\n"
        f"problem: {draft.problem}\n"
        f"solution: {draft.solution}\n"
        f"test_plan: {draft.test_plan}\n"
    )
    try:
        response = litellm.completion(
            model=settings.model_fast,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        content = response.choices[0].message.content or ""
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return draft
        payload = json.loads(content[start : end + 1])
        return PRDraft(
            title=str(payload.get("title") or draft.title),
            problem=str(payload.get("problem") or draft.problem),
            solution=str(payload.get("solution") or draft.solution),
            test_plan=str(payload.get("test_plan") or draft.test_plan),
            issue_number=draft.issue_number,
            repo=draft.repo,
        )
    except Exception:
        return draft


def build_pr_draft(
    issue: IssueContext,
    settings: Settings,
    *,
    scope_hints: list[str] | None = None,
    patch_text: str | None = None,
    commit_message: str | None = None,
) -> PRDraft:
    draft = build_pr_template(
        issue,
        scope_hints=scope_hints,
        patch_text=patch_text,
        commit_message=commit_message,
    )
    return enrich_pr_llm(
        draft,
        issue,
        settings,
        patch_text=patch_text,
        scope_hints=scope_hints,
    )


def render_pr_body(draft: PRDraft) -> str:
    """PR body for gh pr create (excludes markdown H1 title)."""
    return (
        f"## Problem\n\n"
        f"{draft.problem}\n\n"
        f"## Solution\n\n"
        f"{draft.solution}\n\n"
        f"## Test plan\n\n"
        f"{draft.test_plan}\n\n"
        f"---\n\n"
        f"Fixes #{draft.issue_number}\n"
    )


def render_pr_markdown(draft: PRDraft) -> str:
    return f"# {draft.title}\n\n{render_pr_body(draft)}"


def write_pr_md(ctx: RunContext, draft: PRDraft) -> Path:
    path = ctx.artifact_dir / "PR.md"
    path.write_text(render_pr_markdown(draft), encoding="utf-8")
    return path
