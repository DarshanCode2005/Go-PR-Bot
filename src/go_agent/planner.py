"""Planner agent — structured fix plan from issue and context bundle."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from go_agent.config import Settings
from go_agent.context_builder import ContextBundle, SearchArtifact
from go_agent.github_issues import IssueContext
from go_agent.llm_client import complete, llm_available
from go_agent.repo_search import SearchHit
from go_agent.run_context import RunContext
from go_agent.skills import format_skill_prompt
from go_agent.utils import normalize_file_path

PLANNER_SYSTEM_PROMPT = """You are a senior Go open-source maintainer planning a minimal fix for a GitHub issue.

Return JSON only (no markdown fences) with exactly these keys:
- files: list of repository-relative file paths to modify (prefer .go files)
- steps: ordered list of concrete implementation steps
- test_commands: list of shell commands to verify the fix (must include go test)
- acceptance_criteria: list of verifiable conditions for done
- file_dependencies: optional map of file path -> list of paths that file depends on
  (e.g. {"pkg/bar.go": ["pkg/foo.go"]} when bar must be coded after foo). Omit or {} if independent.

When the issue describes behavior changes, validation semantics, bug fixes, or API edge cases:
- Include relevant *_test.go files in files OR name specific tests in acceptance_criteria
  (e.g. "TestUnixAddrValidation passes")
- Reference known tests from context in steps (read test expectations before editing production code)
- Prefer fixing production code to match existing tests unless the issue explicitly asks to change tests
- Do not list test files for pure refactors or docs with no behavior impact

Be specific to the issue and supplied code context. Prefer the smallest correct change."""

_MAX_ISSUE_BODY_CHARS = 3000
_MAX_BUNDLE_ENTRY_CHARS = 2000
_DEFAULT_MAX_CONTEXT_CHARS = 12000
_TEST_FUNC_RE = re.compile(r"func\s+(Test\w+)\s*\(")
_BEHAVIOR_CHANGE_RE = re.compile(
    r"\b(valid(?:ation|ate)?|bug|fail(?:ure|s|ed)?|incorrect|edge\s*case|"
    r"panic|regression|broken|unexpected|should\s+not|does\s+not\s+work)\b",
    re.I,
)
_TEST_AWARENESS_ERROR_MARKER = "Behavior-change issue requires"


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


def _issue_implies_behavior_change(issue: IssueContext) -> bool:
    text = f"{issue.title}\n{issue.body}"
    return bool(_BEHAVIOR_CHANGE_RE.search(text))


def _test_file_text_from_hits(
    path: str,
    search_hits: list[SearchHit],
    *,
    repo_path: Path | None,
) -> str:
    norm = normalize_file_path(path)
    if repo_path is not None:
        file_path = repo_path / path
        if file_path.is_file():
            try:
                return file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return "\n".join(
        hit.line_text
        for hit in search_hits
        if normalize_file_path(hit.path) == norm
    )


def _extract_known_tests(
    context_bundle: ContextBundle,
    search_hits: list[SearchHit] | None = None,
    *,
    repo_path: Path | None = None,
) -> list[tuple[str, str]]:
    """Return (test_name, file_path) pairs found in bundle snippets or search hits."""
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []

    def add(name: str, path: str) -> None:
        key = (name, path)
        if key not in seen:
            seen.add(key)
            ordered.append(key)

    bundle_has_test_file = False
    for entry in context_bundle.files:
        for match in _TEST_FUNC_RE.finditer(entry.content):
            add(match.group(1), entry.path)
        if normalize_file_path(entry.path).endswith("_test.go"):
            bundle_has_test_file = True

    if not bundle_has_test_file and search_hits:
        test_paths = {
            hit.path
            for hit in search_hits
            if normalize_file_path(hit.path).endswith("_test.go")
        }
        for path in sorted(test_paths):
            text = _test_file_text_from_hits(path, search_hits, repo_path=repo_path)
            for match in _TEST_FUNC_RE.finditer(text):
                add(match.group(1), path)

    return ordered


def _format_known_tests_section(
    context_bundle: ContextBundle,
    search_hits: list[SearchHit] | None = None,
    *,
    repo_path: Path | None = None,
) -> str | None:
    known = _extract_known_tests(context_bundle, search_hits, repo_path=repo_path)
    if not known:
        return None
    lines = ["Known tests in context:"]
    lines.extend(f"- {name} ({path})" for name, path in known)
    return "\n".join(lines)


def _plan_has_test_awareness(payload: dict) -> bool:
    files = payload.get("files") or []
    if any(str(item).endswith("_test.go") for item in files):
        return True
    criteria = payload.get("acceptance_criteria") or []
    return any(re.search(r"\bTest[A-Z]\w*", str(item)) for item in criteria)


def _validate_test_awareness(
    payload: dict,
    issue: IssueContext,
    *,
    context_bundle: ContextBundle | None = None,
    search_hits: list[SearchHit] | None = None,
    repo_path: Path | None = None,
) -> None:
    if not _issue_implies_behavior_change(issue):
        return
    if _plan_has_test_awareness(payload):
        return
    known = (
        _extract_known_tests(context_bundle, search_hits, repo_path=repo_path)
        if context_bundle is not None
        else []
    )
    hint = ""
    if known:
        hint = (
            f" Include *_test.go (e.g. {known[0][1]}) "
            "or name tests in acceptance_criteria."
        )
    msg = (
        "Behavior-change issue requires *_test.go in files "
        "or Test* names in acceptance_criteria."
        + hint
    )
    raise PlanError(msg)


def load_search_hits_from_artifact(artifact_dir: Path) -> list[SearchHit]:
    """Load search hits written during scope preparation."""
    path = artifact_dir / "search_hits.json"
    if not path.is_file():
        return []
    try:
        artifact = SearchArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return list(artifact.hits)


def _build_test_awareness_correction(
    error: PlanError,
    context_bundle: ContextBundle,
    search_hits: list[SearchHit] | None,
    *,
    repo_path: Path | None = None,
) -> str:
    known = _extract_known_tests(context_bundle, search_hits, repo_path=repo_path)
    known_lines = "\n".join(f"- {name} ({path})" for name, path in known)
    known_block = f"\nKnown tests in context:\n{known_lines}" if known_lines else ""
    return (
        f"Previous output was invalid: {error}. "
        "This issue changes behavior or validation. Include relevant *_test.go in files "
        "OR list specific test names (Test*) in acceptance_criteria. "
        "Read test expectations before editing production code."
        f"{known_block}\n"
        "Return valid JSON only with keys files, steps, test_commands, "
        "acceptance_criteria, and optional file_dependencies."
    )


def build_planner_messages(
    issue: IssueContext,
    context_bundle: ContextBundle,
    scope_hints: list[str],
    *,
    max_context_chars: int = _DEFAULT_MAX_CONTEXT_CHARS,
    correction: str | None = None,
    search_hits: list[SearchHit] | None = None,
    repo_path: Path | None = None,
) -> list[dict[str, str]]:
    """Build system + user messages for the planner LLM call."""
    skill_section = format_skill_prompt(issue.repo, stage="planner")
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
    known_tests = _format_known_tests_section(
        context_bundle, search_hits, repo_path=repo_path
    )
    if known_tests:
        user_parts.append(known_tests)
    if skill_section:
        user_parts.append(skill_section)
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


def enrich_fix_plan_payload(
    payload: dict,
    *,
    context_bundle: ContextBundle,
    repo_path: Path | None = None,
) -> dict:
    """Sanitize file_dependencies and inject related test files before validation."""
    enriched = dict(payload)
    files = [str(item).strip() for item in (enriched.get("files") or []) if str(item).strip()]
    normalized_files = {normalize_file_path(path) for path in files}

    cleaned_deps: dict[str, list[str]] = {}
    for raw_key, raw_values in (enriched.get("file_dependencies") or {}).items():
        key = normalize_file_path(str(raw_key))
        if key not in normalized_files:
            continue
        cleaned: list[str] = []
        for raw_dep in raw_values or []:
            dep = normalize_file_path(str(raw_dep))
            if dep in normalized_files and dep != key and dep not in cleaned:
                canonical = next(
                    (path for path in files if normalize_file_path(path) == dep),
                    str(raw_dep),
                )
                cleaned.append(canonical)
        if cleaned:
            canonical_key = next(
                (path for path in files if normalize_file_path(path) == key),
                str(raw_key),
            )
            cleaned_deps[canonical_key] = cleaned
    enriched["file_dependencies"] = cleaned_deps

    known_test_files: set[str] = set()
    path_by_norm: dict[str, str] = {}
    for entry in context_bundle.files:
        norm = normalize_file_path(entry.path)
        path_by_norm[norm] = entry.path
        if norm.endswith("_test.go"):
            known_test_files.add(norm)
    if repo_path is not None:
        for test_file in repo_path.rglob("*_test.go"):
            norm = normalize_file_path(test_file.relative_to(repo_path).as_posix())
            known_test_files.add(norm)
            path_by_norm.setdefault(norm, norm)

    additions: list[str] = []
    seen_additions: set[str] = set()
    for path in files:
        norm = normalize_file_path(path)
        if not norm.endswith(".go") or norm.endswith("_test.go"):
            continue
        stem_test = normalize_file_path(
            str(Path(norm).with_name(f"{Path(norm).stem}_test.go"))
        )
        for candidate in sorted(known_test_files):
            if candidate in normalized_files or candidate in seen_additions:
                continue
            same_dir = Path(norm).parent == Path(candidate).parent
            if candidate == stem_test or same_dir:
                seen_additions.add(candidate)
                normalized_files.add(candidate)
                additions.append(path_by_norm.get(candidate, candidate))

    enriched["files"] = files + additions
    return enriched


def _request_plan(
    issue: IssueContext,
    messages: list[dict[str, str]],
    settings: Settings,
    *,
    context_bundle: ContextBundle | None = None,
    repo_path: Path | None = None,
    search_hits: list[SearchHit] | None = None,
) -> FixPlan:
    content = complete(messages, tier="strong", settings=settings)
    if not content:
        raise PlanError("LLM completion failed")
    payload = _parse_plan_json(content)
    _validate_test_awareness(
        payload,
        issue,
        context_bundle=context_bundle,
        search_hits=search_hits,
        repo_path=repo_path,
    )
    if context_bundle is not None:
        payload = enrich_fix_plan_payload(
            payload,
            context_bundle=context_bundle,
            repo_path=repo_path,
        )
    return _validate_fix_plan(payload, issue)


def build_fix_plan(
    issue: IssueContext,
    context_bundle: ContextBundle,
    scope_hints: list[str],
    settings: Settings,
    logger: logging.Logger | None = None,
    *,
    repo_path: Path | None = None,
    search_hits: list[SearchHit] | None = None,
) -> FixPlan:
    """Build and validate a structured fix plan; raises PlanError on failure."""
    log = logger or logging.getLogger("go_agent")
    if not llm_available(settings):
        raise PlanError("LLM API key required for planner")

    messages = build_planner_messages(
        issue,
        context_bundle,
        scope_hints,
        search_hits=search_hits,
        repo_path=repo_path,
    )
    try:
        plan = _request_plan(
            issue,
            messages,
            settings,
            context_bundle=context_bundle,
            repo_path=repo_path,
            search_hits=search_hits,
        )
        log.info(
            "Fix plan built: %d files, %d steps",
            len(plan.files),
            len(plan.steps),
        )
        return plan
    except PlanError as first_error:
        log.warning("Planner first attempt failed: %s", first_error)
        if _TEST_AWARENESS_ERROR_MARKER in str(first_error):
            correction = _build_test_awareness_correction(
                first_error,
                context_bundle,
                search_hits,
                repo_path=repo_path,
            )
        else:
            correction = (
                f"Previous output was invalid: {first_error}. "
                "Return valid JSON only with keys files, steps, test_commands, "
                "acceptance_criteria, and optional file_dependencies."
            )
        retry_messages = build_planner_messages(
            issue,
            context_bundle,
            scope_hints,
            correction=correction,
            search_hits=search_hits,
            repo_path=repo_path,
        )
        try:
            plan = _request_plan(
                issue,
                retry_messages,
                settings,
                context_bundle=context_bundle,
                repo_path=repo_path,
                search_hits=search_hits,
            )
            log.info("Fix plan built on retry")
            return plan
        except PlanError as retry_error:
            raise PlanError(f"Planner failed after retry: {retry_error}") from retry_error


def write_plan(ctx: RunContext, plan: FixPlan) -> Path:
    path = ctx.artifact_dir / "plan.json"
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
