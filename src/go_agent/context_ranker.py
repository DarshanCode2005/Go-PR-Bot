"""Rank repository files using weighted BFS over the code graph."""

from __future__ import annotations

from collections import deque

from pydantic import BaseModel

from go_agent.code_graph import CodeGraph, edge_rationale, neighbors, node_path
from go_agent.config import Settings
from go_agent.repo_search import SearchHit


class RankedFile(BaseModel):
    path: str
    score: float
    graph_distance: int
    rationale: str


def _hit_paths(search_hits: list[SearchHit]) -> set[str]:
    return {hit.path for hit in search_hits}


def _test_pair_path(path: str) -> str | None:
    if path.endswith("_test.go"):
        return path[: -len("_test.go")] + ".go"
    if path.endswith(".go") and not path.endswith("_test.go"):
        parts = path.rsplit("/", 1)
        if len(parts) == 2:
            return f"{parts[0]}/{parts[1][:-3]}_test.go"
        return f"{path[:-3]}_test.go"
    return None


def rank_files(
    graph: CodeGraph,
    search_hits: list[SearchHit],
    settings: Settings,
) -> list[RankedFile]:
    """Rank files by weighted BFS from graph seed nodes."""
    if not graph.seeds:
        hit_only = sorted(_hit_paths(search_hits))
        return [
            RankedFile(
                path=path,
                score=110.0,
                graph_distance=0,
                rationale="ripgrep hit",
            )
            for path in hit_only[: settings.context_max_files]
        ]

    hit_paths = _hit_paths(search_hits)
    hit_queries = {hit.path: hit.query for hit in search_hits}
    base_scores = {0: 100.0, 1: 70.0, 2: 40.0}

    best: dict[str, RankedFile] = {}
    queue: deque[tuple[str, int, str | None]] = deque()

    for seed in graph.seeds:
        queue.append((seed, 0, None))

    while queue:
        node_id, distance, via_kind = queue.popleft()
        if distance > settings.context_graph_max_hops:
            continue
        path = node_path(node_id)
        score = base_scores.get(distance, 20.0)
        if path in hit_paths:
            score += 10.0

        if distance == 0:
            if path in hit_paths:
                rationale = f"ripgrep hit for {hit_queries.get(path, path)}"
            else:
                rationale = "issue hint"
        else:
            rationale = edge_rationale(via_kind or "imports")

        existing = best.get(path)
        if existing is None or score > existing.score or (
            score == existing.score and distance < existing.graph_distance
        ):
            best[path] = RankedFile(
                path=path,
                score=score,
                graph_distance=distance,
                rationale=rationale,
            )

        for neighbor_id, kind in neighbors(graph, node_id):
            queue.append((neighbor_id, distance + 1, kind))

    ranked = sorted(
        best.values(),
        key=lambda item: (-item.score, item.graph_distance, item.path),
    )

    selected: list[RankedFile] = []
    selected_paths: set[str] = set()
    for item in ranked:
        if len(selected) >= settings.context_max_files:
            break
        selected.append(item)
        selected_paths.add(item.path)
        if item.path.endswith(".go") and not item.path.endswith("_test.go"):
            test_path = _test_pair_path(item.path)
            if test_path and test_path not in selected_paths:
                selected.append(
                    RankedFile(
                        path=test_path,
                        score=item.score - 5.0,
                        graph_distance=item.graph_distance,
                        rationale="paired test",
                    )
                )
                selected_paths.add(test_path)

    selected.sort(key=lambda item: (-item.score, item.graph_distance, item.path))
    return selected[: settings.context_max_files]
