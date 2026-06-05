"""Tests for repository file tree and go.mod summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.repo_map import (
    build_file_tree,
    build_repo_map,
    list_top_level_packages,
    parse_go_mod,
    write_repo_map,
)
from go_agent.run_context import create_run_context


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _make_fixture_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    (repo / "go.mod").write_text(
        "module example.com/foo\n\ngo 1.22\n",
        encoding="utf-8",
    )
    (repo / "main.go").write_text("package main\n", encoding="utf-8")

    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "pkg.go").write_text("package pkg\n", encoding="utf-8")
    nested = pkg / "nested" / "deep"
    nested.mkdir(parents=True)
    (nested / "file.go").write_text("package deep\n", encoding="utf-8")

    internal = repo / "internal"
    internal.mkdir()
    (internal / "util.go").write_text("package internal\n", encoding="utf-8")

    vendor = repo / "vendor"
    vendor.mkdir()
    (vendor / "stale.go").write_text("package vendor\n", encoding="utf-8")

    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    return repo


def _child_names(node) -> list[str]:
    return [child.name for child in node.children]


def _find_child(node, name: str):
    for child in node.children:
        if child.name == name:
            return child
    return None


def test_parse_go_mod(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    summary = parse_go_mod(repo)
    assert summary.module_path == "example.com/foo"
    assert summary.go_version == "1.22"


def test_parse_go_mod_missing(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    summary = parse_go_mod(repo)
    assert summary.module_path is None
    assert summary.go_version is None


def test_build_file_tree_depth_limit(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    tree = build_file_tree(repo, max_depth=2, skip_vendor=True)
    pkg = _find_child(tree, "pkg")
    assert pkg is not None
    nested = _find_child(pkg, "nested")
    assert nested is not None
    assert nested.children == []


def test_skips_git_and_vendor(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    tree = build_file_tree(repo, max_depth=4, skip_vendor=True)
    names = _child_names(tree)
    assert ".git" not in names
    assert "vendor" not in names
    assert "internal" in names
    assert "pkg" in names


def test_list_top_level_packages(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    packages = list_top_level_packages(repo, skip_vendor=True)
    assert packages == ["internal", "pkg"]


def test_list_top_level_packages_ignores_symlinked_go(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "remote.go").write_text("package outside\n", encoding="utf-8")
    (repo / "fake_pkg").symlink_to(outside, target_is_directory=True)
    packages = list_top_level_packages(repo, skip_vendor=True)
    assert packages == ["internal", "pkg"]


def test_build_repo_map(tmp_path):
    repo = _make_fixture_repo(tmp_path)
    settings = Settings(repo_map_max_depth=3, repo_map_skip_vendor=True)
    repo_map = build_repo_map(repo, "owner/repo", settings)
    assert repo_map.repo == "owner/repo"
    assert repo_map.go_mod.module_path == "example.com/foo"
    assert repo_map.top_level_packages == ["internal", "pkg"]
    assert repo_map.skipped_dirs == [".git", "vendor"]
    assert repo_map.tree_depth == 3


def test_write_repo_map_creates_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = _make_fixture_repo(tmp_path)
    ctx = create_run_context()
    repo_map = build_repo_map(repo, "owner/repo", Settings())
    path = write_repo_map(ctx, repo_map)
    assert path == ctx.artifact_dir / "repo_map.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["repo"] == "owner/repo"
    assert payload["go_mod"]["module_path"] == "example.com/foo"
    assert "internal" in payload["top_level_packages"]
