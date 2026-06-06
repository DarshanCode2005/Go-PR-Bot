"""LangGraph SqliteSaver checkpointer for run resume."""

from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from go_agent.config import Settings, get_settings

_CONNECTIONS: dict[str, sqlite3.Connection] = {}


def checkpoints_db_path(settings: Settings | None = None) -> str:
    """Return the shared SQLite checkpoint database path."""
    settings = settings or get_settings()
    return str((settings.artifacts_dir / "checkpoints" / "checkpoints.db").resolve())


def graph_invoke_config(run_id: str) -> dict[str, Any]:
    """Build LangGraph invoke config with thread_id = run_id."""
    return {"configurable": {"thread_id": run_id}}


def get_graph_state(compiled: Any, run_id: str) -> Any:
    """Return the latest checkpoint snapshot for a run thread."""
    return compiled.get_state(graph_invoke_config(run_id))


def is_run_complete(snapshot: Any) -> bool:
    """True when the graph has no pending nodes for this thread."""
    return not snapshot.next


@lru_cache(maxsize=1)
def create_checkpointer(db_path: str) -> SqliteSaver:
    """Open or reuse a persistent SqliteSaver for the checkpoint database."""
    if db_path in _CONNECTIONS:
        return SqliteSaver(_CONNECTIONS[db_path])

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    _CONNECTIONS[db_path] = conn
    return saver


def get_checkpointer(settings: Settings | None = None) -> SqliteSaver:
    """Return the shared checkpointer for the configured artifacts directory."""
    return create_checkpointer(checkpoints_db_path(settings))


def clear_checkpointer_cache() -> None:
    """Clear cached checkpointer connections (for tests)."""
    create_checkpointer.cache_clear()
    _CONNECTIONS.clear()
