"""Lightweight in-memory code graph for context expansion."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.repo_map import _should_skip_dir, parse_go_mod
from go_agent.repo_search import SearchHit
from go_agent.run_context import RunContext

_GO_FILE = re.compile(r"^[\w./-]+\.go$")
_IMPORT = re.compile(r'"([^"]+)"')
_SKIP_FILE_SUFFIXES = (".pb.go", "_gen.go")
_SKIP_FILE_PARTS = ("bindata",)


class GraphNode(BaseModel):
    id: str
    kind: Literal["file", "package", "hint"]
    label: str


class GraphEdge(BaseModel):
    source: str
    target: str
    kind: Literal["imports", "in_package", "tests"]


class CodeGraph(BaseModel):
    repo: str
    module_path: str | None = None
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    seeds: list[str] = Field(default_factory=list)


def file_node_id(path: str) -> str:
    return f"file:{path}"


def _should_skip_file(name: str) -> bool:
    if any(name.endswith(suffix) for suffix in _SKIP_FILE_SUFFIXES):
        return True
    lower = name.lower()
    return any(part in lower for part in _SKIP_FILE_PARTS)


def _collect_go_files(repo_path: Path, *, skip_vendor: bool) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [
            name
            for name in dirnames
            if not _should_skip_dir(name, skip_vendor=skip_vendor)
        ]
        rel_dir = Path(dirpath).relative_to(repo_path)
        for name in filenames:
            if not name.endswith(".go") or _should_skip_file(name):
                continue
            rel = str(rel_dir / name).replace("\\", "/")
            if rel == ".":
                rel = name
            elif rel.startswith("./"):
                rel = rel[2:]
            files.append(rel)
    return sorted(files)


def _parse_imports(text: str) -> list[str]:
    imports: list[str] = []
    for match in _IMPORT.finditer(text):
        value = match.group(1)
        if "/" in value or "." in value:
            imports.append(value)
    return imports


def _import_to_repo_path(import_path: str, module_path: str | None, known_files: set[str]) -> str | None:
    if module_path and import_path.startswith(module_path):
        suffix = import_path.removeprefix(module_path).strip("/")
        if not suffix:
            return None
        candidate = f"{suffix}.go"
        if candidate in known_files:
            return candidate
        for path in known_files:
            if path.endswith(f"/{suffix}.go") or path == f"{suffix}/main.go":
                return path
        dir_candidate = suffix
        for path in known_files:
            if path.startswith(f"{dir_candidate}/"):
                return path
    tail = import_path.rsplit("/", 1)[-1]
    for path in known_files:
        if path.endswith(f"/{tail}.go") or path == f"{tail}.go":
            return path
    return None


def _test_pair_path(path: str) -> str | None:
    if path.endswith("_test.go"):
        base = path[: -len("_test.go")] + ".go"
        return base
    if path.endswith(".go") and not path.endswith("_test.go"):
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1][:-3]}_test.go"
        return f"{path[:-3]}_test.go"
    return None


def _hint_to_file_path(hint: str) -> str | None:
    cleaned = hint.strip().strip("`")
    if _GO_FILE.match(cleaned):
        return cleaned
    return None


def _add_edge(edges: list[GraphEdge], seen: set[tuple[str, str, str]], source: str, target: str, kind: str) -> None:
    key = (source, target, kind)
    if key in seen:
        return
    seen.add(key)
    edges.append(GraphEdge(source=source, target=target, kind=kind))


def build_code_graph(
    repo_path: Path,
    repo: str,
    scope_hints: list[str],
    search_hits: list[SearchHit],
    settings: Settings,
) -> CodeGraph:
    """Build a lightweight code graph from scope hints and search hits."""
    module = parse_go_mod(repo_path).module_path
    go_files = _collect_go_files(repo_path, skip_vendor=settings.repo_map_skip_vendor)
    known = set(go_files)

    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []
    edge_seen: set[tuple[str, str, str]] = set()
    seeds: list[str] = []

    def ensure_file_node(path: str) -> str:
        node_id = file_node_id(path)
        if node_id not in nodes:
            nodes[node_id] = GraphNode(id=node_id, kind="file", label=path)
        return node_id

    for path in go_files:
        ensure_file_node(path)

    for hint in scope_hints:
        path = _hint_to_file_path(hint)
        if not path or path not in known:
            continue
        node_id = ensure_file_node(path)
        if node_id not in seeds:
            seeds.append(node_id)

    for hit in search_hits:
        if hit.path not in known:
            continue
        node_id = ensure_file_node(hit.path)
        if node_id not in seeds:
            seeds.append(node_id)

    by_dir: dict[str, list[str]] = {}
    for path in go_files:
        parent = str(Path(path).parent)
        if parent == ".":
            parent = ""
        by_dir.setdefault(parent, []).append(path)

    for paths in by_dir.values():
        if len(paths) < 2:
            continue
        ids = [ensure_file_node(path) for path in paths]
        for i, source in enumerate(ids):
            for target in ids[i + 1 :]:
                _add_edge(edges, edge_seen, source, target, "in_package")
                _add_edge(edges, edge_seen, target, source, "in_package")

    for path in go_files:
        paired = _test_pair_path(path)
        if paired and paired in known:
            source = ensure_file_node(path)
            target = ensure_file_node(paired)
            _add_edge(edges, edge_seen, source, target, "tests")
            _add_edge(edges, edge_seen, target, source, "tests")

    for path in go_files:
        full_path = repo_path / path
        try:
            text = full_path.read_text(encoding="utf-8")
        except OSError:
            continue
        source_id = ensure_file_node(path)
        for import_path in _parse_imports(text):
            target_path = _import_to_repo_path(import_path, module, known)
            if not target_path:
                continue
            target_id = ensure_file_node(target_path)
            _add_edge(edges, edge_seen, source_id, target_id, "imports")

    return CodeGraph(
        repo=repo,
        module_path=module,
        nodes=list(nodes.values()),
        edges=edges,
        seeds=seeds,
    )


def write_code_graph(ctx: RunContext, graph: CodeGraph) -> Path:
    path = ctx.artifact_dir / "code_graph.json"
    path.write_text(graph.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def neighbors(graph: CodeGraph, node_id: str) -> list[tuple[str, str]]:
    """Return (neighbor_id, edge_kind) pairs for undirected traversal."""
    out: list[tuple[str, str]] = []
    for edge in graph.edges:
        if edge.source == node_id and edge.target != node_id:
            out.append((edge.target, edge.kind))
        elif edge.target == node_id and edge.source != node_id:
            out.append((edge.source, edge.kind))
    return out


def node_path(node_id: str) -> str:
    return node_id.removeprefix("file:")


def structural_summary(graph: CodeGraph, path: str) -> str:
    """One-line structural description from graph edges."""
    node_id = file_node_id(path)
    parts: list[str] = []
    for edge in graph.edges:
        if edge.source == node_id and edge.kind == "imports":
            parts.append(f"imports {node_path(edge.target)}")
        elif edge.target == node_id and edge.kind == "tests":
            parts.append(f"tested by {node_path(edge.source)}")
        elif edge.source == node_id and edge.kind == "tests":
            parts.append(f"tests {node_path(edge.target)}")
    if not parts:
        return f"Go file {path}"
    return f"{path}: " + "; ".join(sorted(set(parts)))


def edge_rationale(kind: str) -> str:
    mapping = {
        "tests": "paired test",
        "in_package": "same package",
        "imports": "imports",
    }
    return mapping.get(kind, kind)
