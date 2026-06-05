"""Build repository file tree and go.mod summary for agent context."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.run_context import RunContext

_MODULE_RE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)
_GO_VERSION_RE = re.compile(r"^\s*go\s+(\S+)", re.MULTILINE)


class TreeNode(BaseModel):
    name: str
    type: Literal["dir", "file"]
    children: list[TreeNode] = Field(default_factory=list)


class GoModSummary(BaseModel):
    module_path: str | None = None
    go_version: str | None = None


class RepoMap(BaseModel):
    repo: str
    repo_path: str
    go_mod: GoModSummary
    tree_depth: int
    tree: TreeNode
    top_level_packages: list[str]
    skipped_dirs: list[str]


def parse_go_mod(repo_path: Path) -> GoModSummary:
    """Extract module path and Go version from go.mod when present."""
    go_mod_path = repo_path / "go.mod"
    if not go_mod_path.is_file():
        return GoModSummary()

    text = go_mod_path.read_text(encoding="utf-8")
    module_match = _MODULE_RE.search(text)
    go_match = _GO_VERSION_RE.search(text)
    return GoModSummary(
        module_path=module_match.group(1) if module_match else None,
        go_version=go_match.group(1) if go_match else None,
    )


def _skipped_dir_names(skip_vendor: bool) -> list[str]:
    names = [".git"]
    if skip_vendor:
        names.append("vendor")
    return names


def _should_skip_dir(name: str, *, skip_vendor: bool) -> bool:
    if name == ".git":
        return True
    return skip_vendor and name == "vendor"


def _dir_has_go_files(path: Path) -> bool:
    stack = [path]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False) and entry.name.endswith(".go"):
                        return True
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(Path(entry.path))
        except OSError:
            continue
    return False


def build_file_tree(
    repo_path: Path,
    max_depth: int,
    *,
    skip_vendor: bool = True,
) -> TreeNode:
    """Build a depth-limited directory tree rooted at repo_path."""

    def walk(directory: Path, depth: int) -> TreeNode:
        children: list[TreeNode] = []
        if depth >= max_depth:
            return TreeNode(name=directory.name or ".", type="dir", children=children)

        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda entry: (not entry.is_dir(follow_symlinks=False), entry.name),
            )
        except OSError:
            return TreeNode(name=directory.name or ".", type="dir", children=children)

        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if _should_skip_dir(entry.name, skip_vendor=skip_vendor):
                    continue
                children.append(walk(Path(entry.path), depth + 1))
            elif entry.is_file(follow_symlinks=False):
                children.append(TreeNode(name=entry.name, type="file"))

        return TreeNode(name=directory.name or ".", type="dir", children=children)

    return walk(repo_path.resolve(), 0)



def list_top_level_packages(repo_path: Path, *, skip_vendor: bool = True) -> list[str]:
    """Return root-level directories that contain Go source files."""
    packages: list[str] = []
    try:
        entries = sorted(os.scandir(repo_path), key=lambda entry: entry.name)
    except OSError:
        return packages

    for entry in entries:
        if not entry.is_dir(follow_symlinks=False) or entry.is_symlink():
            continue
        if _should_skip_dir(entry.name, skip_vendor=skip_vendor):
            continue
        if _dir_has_go_files(Path(entry.path)):
            packages.append(entry.name)
    return packages


def build_repo_map(repo_path: Path, repo: str, settings: Settings) -> RepoMap:
    """Compose go.mod summary, file tree, and top-level packages."""
    skip_vendor = settings.repo_map_skip_vendor
    max_depth = settings.repo_map_max_depth
    return RepoMap(
        repo=repo,
        repo_path=str(repo_path.resolve()),
        go_mod=parse_go_mod(repo_path),
        tree_depth=max_depth,
        tree=build_file_tree(repo_path, max_depth, skip_vendor=skip_vendor),
        top_level_packages=list_top_level_packages(repo_path, skip_vendor=skip_vendor),
        skipped_dirs=_skipped_dir_names(skip_vendor),
    )


def write_repo_map(ctx: RunContext, repo_map: RepoMap) -> Path:
    path = ctx.artifact_dir / "repo_map.json"
    path.write_text(repo_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
