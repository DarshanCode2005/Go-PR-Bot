"""Ripgrep wrapper for repository code search."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.issue_scope import _NOISE

_MIN_QUERY_LEN = 3


class RipgrepError(RuntimeError):
    """Raised when ripgrep fails or times out."""


class RipgrepNotFoundError(RipgrepError):
    """Raised when the rg binary is not installed."""


class SearchHit(BaseModel):
    path: str
    line_number: int
    line_text: str
    query: str


class SearchResponse(BaseModel):
    query: str
    glob: str | None
    hits: list[SearchHit] = Field(default_factory=list)
    truncated: bool = False


def _parse_rg_line(line: str, query: str) -> SearchHit | None:
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split(":", 2)
    if len(parts) < 3:
        return None
    path, line_no, content = parts
    try:
        number = int(line_no)
    except ValueError:
        return None
    return SearchHit(path=path, line_number=number, line_text=content, query=query)


def _build_rg_args(
    query: str,
    settings: Settings,
    *,
    glob: str | None,
    max_results: int,
) -> list[str]:
    glob_pattern = glob if glob is not None else settings.ripgrep_default_glob
    args = [
        "rg",
        "--no-config",
        "--color",
        "never",
        "--line-number",
        "--no-heading",
        "--fixed-strings",
        "--max-total-count",
        str(max_results),
    ]
    if glob_pattern:
        args.extend(["--glob", glob_pattern])
    if settings.repo_map_skip_vendor:
        args.extend(["--glob", "!vendor/**", "--glob", "!.git/**"])
    args.extend([query, "."])
    return args


def search_repo(
    repo_path: Path,
    query: str,
    settings: Settings,
    *,
    glob: str | None = None,
    max_results: int | None = None,
    timeout: int | None = None,
) -> SearchResponse:
    """Search repo_path with ripgrep and return structured hits."""
    if not shutil.which("rg"):
        raise RipgrepNotFoundError(
            "Install ripgrep (`rg`) to search the repository. "
            "See https://github.com/BurntSushi/ripgrep#installation"
        )

    limit = max_results if max_results is not None else settings.ripgrep_max_results
    timeout_secs = timeout if timeout is not None else settings.ripgrep_timeout
    glob_pattern = glob if glob is not None else settings.ripgrep_default_glob
    args = _build_rg_args(query, settings, glob=glob, max_results=limit)

    try:
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RipgrepError(f"ripgrep timed out after {timeout_secs}s") from exc

    if result.returncode not in (0, 1):
        stderr = (result.stderr or "").strip()
        raise RipgrepError(stderr or f"ripgrep failed with exit code {result.returncode}")

    hits: list[SearchHit] = []
    for line in result.stdout.splitlines():
        hit = _parse_rg_line(line, query)
        if hit is not None:
            hits.append(hit)

    truncated = len(hits) >= limit
    return SearchResponse(
        query=query,
        glob=glob_pattern,
        hits=hits[:limit],
        truncated=truncated,
    )


def _is_searchable_hint(hint: str) -> bool:
    cleaned = hint.strip()
    if len(cleaned) < _MIN_QUERY_LEN:
        return False
    if cleaned.lower() in _NOISE:
        return False
    return True


def _dedupe_queries(hints: list[str], max_queries: int) -> list[str]:
    seen: set[str] = set()
    queries: list[str] = []
    for hint in hints:
        if not _is_searchable_hint(hint):
            continue
        key = hint.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(hint)
        if len(queries) >= max_queries:
            break
    return queries


def search_scope_hints(
    repo_path: Path,
    hints: list[str],
    settings: Settings,
    *,
    max_queries: int = 10,
) -> list[SearchHit]:
    """Run ripgrep for each scope hint and merge deduplicated hits."""
    merged: list[SearchHit] = []
    seen: set[tuple[str, int, str]] = set()
    for query in _dedupe_queries(hints, max_queries):
        try:
            response = search_repo(repo_path, query, settings)
        except RipgrepNotFoundError:
            raise
        except RipgrepError:
            continue
        for hit in response.hits:
            key = (hit.path, hit.line_number, hit.query)
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)
    return merged
