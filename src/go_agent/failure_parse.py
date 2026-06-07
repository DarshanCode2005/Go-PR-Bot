"""Parse test/lint failure output and resolve test file paths."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from go_agent.config import Settings
from go_agent.repo_search import RipgrepNotFoundError, RipgrepError, search_repo
from go_agent.utils import normalize_file_path

GO_FILE_RE = re.compile(r"(?:\./)?([\w./-]+\.go)")
_FAIL_TEST = re.compile(r"--- FAIL:\s+(\w+)", re.MULTILINE)
_FAIL_TEST_ALT = re.compile(r"^\s*FAIL:\s+(\w+)", re.MULTILINE)
_FAIL_PACKAGE = re.compile(r"^FAIL\t(\S+)", re.MULTILINE)
_FILE_LINE = re.compile(r"([\w./-]+\.go):(\d+)", re.MULTILINE)


def parse_failing_tests(test_output: str) -> list[str]:
    """Extract failing test function names from go test output."""
    names: list[str] = []
    seen: set[str] = set()
    for pattern in (_FAIL_TEST, _FAIL_TEST_ALT):
        for match in pattern.finditer(test_output):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def parse_failing_packages(test_output: str) -> list[str]:
    """Extract import paths from go test summary lines (FAIL\\tpath)."""
    packages: list[str] = []
    seen: set[str] = set()
    for match in _FAIL_PACKAGE.finditer(test_output):
        path = match.group(1)
        if path not in seen:
            seen.add(path)
            packages.append(path)
    return packages


def parse_referenced_go_files(output: str) -> list[str]:
    """Extract Go file paths from test/lint output (paths and file:line references)."""
    paths: list[str] = []
    seen: set[str] = set()
    for match in GO_FILE_RE.finditer(output):
        norm = normalize_file_path(match.group(1))
        if norm not in seen:
            seen.add(norm)
            paths.append(norm)
    for match in _FILE_LINE.finditer(output):
        norm = normalize_file_path(match.group(1))
        if norm not in seen:
            seen.add(norm)
            paths.append(norm)
    return paths


def resolve_test_files(
    repo_path: Path,
    test_names: list[str],
    settings: Settings,
    *,
    logger: logging.Logger | None = None,
) -> list[str]:
    """Map test names to *_test.go files via ripgrep for func TestName definitions."""
    log = logger or logging.getLogger("go_agent")
    resolved: list[str] = []
    seen: set[str] = set()
    for test_name in test_names:
        query = f"func {test_name}"
        try:
            response = search_repo(
                repo_path,
                query,
                settings,
                glob="*_test.go",
                max_results=5,
            )
        except RipgrepNotFoundError:
            log.warning("ripgrep not available; cannot resolve test file for %s", test_name)
            return resolved
        except RipgrepError as exc:
            log.warning("ripgrep failed resolving %s: %s", test_name, exc)
            continue
        for hit in response.hits:
            norm = normalize_file_path(hit.path)
            if norm in seen:
                continue
            if not (repo_path / norm).is_file():
                continue
            seen.add(norm)
            resolved.append(norm)
    return resolved


__all__ = [
    "GO_FILE_RE",
    "parse_failing_packages",
    "parse_failing_tests",
    "parse_referenced_go_files",
    "resolve_test_files",
]
