"""Clone approved GitHub repos into per-run workspaces with shared caching."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from go_agent.constants import APPROVED_REPOS, APPROVED_REPOS_HELP
from go_agent.run_context import RunContext

_GIT_TIMEOUT = 300
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class RepoNotAllowedError(ValueError):
    """Raised when repo is not on the assignment allowlist."""


class CloneError(RuntimeError):
    """Raised when git clone or remote resolution fails."""


def assert_repo_allowed(repo: str) -> None:
    if repo not in APPROVED_REPOS:
        raise RepoNotAllowedError(
            f"Repository {repo!r} is not allowed. Approved repos: {APPROVED_REPOS_HELP}"
        )


def repo_slug(repo: str) -> str:
    return repo.replace("/", "__")


def github_url(repo: str) -> str:
    return f"https://github.com/{repo}.git"


def _run_git(args: list[str], *, cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise CloneError(stderr or f"git {' '.join(args)} failed") from exc
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"git timed out after {_GIT_TIMEOUT}s") from exc
    return result.stdout.strip()


def resolve_remote_head(repo_url: str) -> str:
    """Return the commit SHA at the remote default branch HEAD."""
    output = _run_git(["ls-remote", "--symref", repo_url, "HEAD"])
    for line in output.splitlines():
        if line.startswith("ref:"):
            continue
        parts = line.split()
        if parts and _SHA_RE.match(parts[0]):
            return parts[0]
    raise CloneError(f"could not resolve remote HEAD for {repo_url}")


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _cache_meta_path(cache_dir: Path) -> Path:
    return cache_dir / "meta.json"


def _read_cache_meta(cache_dir: Path) -> dict[str, Any] | None:
    meta_path = _cache_meta_path(cache_dir)
    if not meta_path.is_file():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache_meta(cache_dir: Path, repo: str, remote_head: str) -> None:
    meta = {"repo": repo, "remote_head": remote_head}
    _cache_meta_path(cache_dir).write_text(
        json.dumps(meta, indent=2) + "\n",
        encoding="utf-8",
    )


def _cache_valid(cache_dir: Path, remote_head: str) -> bool:
    if not _is_git_repo(cache_dir):
        return False
    meta = _read_cache_meta(cache_dir)
    if not meta:
        return False
    if meta.get("remote_head") != remote_head:
        return False
    try:
        local_head = _run_git(["rev-parse", "HEAD"], cwd=cache_dir)
    except CloneError:
        return False
    return local_head == remote_head


def _remove_path(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _shallow_clone(repo_url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _remove_path(dest)
    _run_git(["clone", "--depth", "1", repo_url, str(dest)])


def _local_clone(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        _remove_path(dest)
    _run_git(["clone", "--local", str(source), str(dest)])


def _update_cache(repo: str, repo_url: str, remote_head: str, cache_dir: Path, logger: logging.Logger) -> None:
    logger.info("Updating cache for %s", repo)
    _remove_path(cache_dir)
    _shallow_clone(repo_url, cache_dir)
    _write_cache_meta(cache_dir, repo, remote_head)


def ensure_repo_cloned(
    repo: str,
    ctx: RunContext,
    logger: logging.Logger,
    *,
    repo_url: str | None = None,
) -> Path:
    """Clone repo into ctx.workspace_dir/repo, using a shared cache when possible."""
    assert_repo_allowed(repo)
    dest = ctx.workspace_dir / "repo"
    if _is_git_repo(dest):
        logger.info("Repo already present at %s", dest)
        return dest

    url = repo_url or github_url(repo)
    remote_head = resolve_remote_head(url)
    cache_dir = ctx.settings.work_dir / "_cache" / repo_slug(repo)
    cache_hit = _cache_valid(cache_dir, remote_head)

    if cache_hit:
        logger.info(
            "Using cached clone for %s at %s",
            repo,
            remote_head[:8],
        )
    else:
        _update_cache(repo, url, remote_head, cache_dir, logger)

    _local_clone(cache_dir, dest)

    meta = {
        "repo": repo,
        "remote_head": remote_head,
        "repo_path": str(dest),
        "cache_hit": cache_hit,
        "repo_url": url,
    }
    (ctx.artifact_dir / "repo_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("Cloned %s to %s", repo, dest)
    return dest
