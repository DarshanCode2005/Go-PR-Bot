"""Extract scope hints from GitHub issue text."""

from __future__ import annotations

import json
import re

from go_agent.config import Settings
from go_agent.github_issues import IssueContext
from go_agent.llm_client import complete

MAX_SCOPE_HINTS = 50

_BACKTICK = re.compile(r"`([^`\n]+)`")
_GO_FILE = re.compile(r"\b(?:[\w.-]+/)*[\w.-]+\.go\b")
_PACKAGE = re.compile(r"\bpackage\s+([\w./-]+)", re.MULTILINE)
_FUNC_DECL = re.compile(r"\bfunc\s+(?:\([^)]*\)\s+)?([\w.]+)")
_GITHUB_IMPORT = re.compile(r"github\.com/[\w./-]+")
_PANIC_ERROR = re.compile(r"(?:panic|error):\s*[^\n`\"]{3,120}", re.IGNORECASE)
_QUOTED = re.compile(r'"([^"\n]{3,120})"')
_NOISE = frozenset({"http", "https", "go", "the", "and", "or"})


def _issue_text(issue: IssueContext) -> str:
    parts = [issue.title, issue.body]
    parts.extend(comment.body for comment in issue.comments)
    return "\n".join(part for part in parts if part)


def _normalize_hint(raw: str) -> str | None:
    hint = raw.strip().strip(".,;:")
    if len(hint) < 2:
        return None
    if hint.lower() in _NOISE:
        return None
    if hint.startswith("http://") or hint.startswith("https://"):
        return None
    return hint


def _dedupe_preserve_order(hints: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for hint in hints:
        key = hint.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(hint)
    return out[:MAX_SCOPE_HINTS]


def extract_scope_hints(issue: IssueContext) -> list[str]:
    """Heuristically extract file paths, symbols, packages, and errors from issue text."""
    text = _issue_text(issue)
    hints: list[str] = []

    for match in _BACKTICK.finditer(text):
        normalized = _normalize_hint(match.group(1))
        if normalized:
            hints.append(normalized)

    for match in _GO_FILE.finditer(text):
        normalized = _normalize_hint(match.group(0))
        if normalized:
            hints.append(normalized)

    for match in _PACKAGE.finditer(text):
        normalized = _normalize_hint(match.group(1))
        if normalized:
            hints.append(normalized)
            hints.append(f"package {normalized}")

    for match in _FUNC_DECL.finditer(text):
        normalized = _normalize_hint(match.group(1))
        if normalized:
            hints.append(normalized)

    for match in _GITHUB_IMPORT.finditer(text):
        normalized = _normalize_hint(match.group(0))
        if normalized:
            hints.append(normalized)

    for match in _PANIC_ERROR.finditer(text):
        normalized = _normalize_hint(match.group(0))
        if normalized:
            hints.append(normalized)

    for match in _QUOTED.finditer(text):
        normalized = _normalize_hint(match.group(1))
        if normalized and any(ch.isalpha() for ch in normalized):
            hints.append(normalized)

    return _dedupe_preserve_order(hints)


def enrich_scope_hints_llm(
    issue: IssueContext,
    hints: list[str],
    settings: Settings,
) -> list[str]:
    """Optionally ask a fast LLM for additional scope hints; fallback to heuristics on failure."""
    prompt = (
        "Extract additional Go code scope hints (file paths, func names, packages, error strings) "
        "from this GitHub issue. Return JSON only: {\"scope_hints\": [\"...\"]}.\n\n"
        f"Title: {issue.title}\n\nBody:\n{issue.body[:2000]}\n\n"
        f"Existing hints: {hints[:20]}"
    )
    try:
        content = complete(
            messages=[{"role": "user", "content": prompt}],
            tier="fast",
            settings=settings,
            temperature=0,
        )
        if not content:
            return hints
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return hints
        payload = json.loads(content[start : end + 1])
        extra = payload.get("scope_hints", [])
        if not isinstance(extra, list):
            return hints
        merged = hints + [str(item) for item in extra if item]
        return _dedupe_preserve_order(merged)
    except Exception:
        return hints


def build_scope_hints(issue: IssueContext, settings: Settings) -> list[str]:
    hints = extract_scope_hints(issue)
    return enrich_scope_hints_llm(issue, hints, settings)
