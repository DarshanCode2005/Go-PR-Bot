"""Per-run workspace and artifact paths."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from go_agent.config import Settings, get_settings


@dataclass(frozen=True)
class RunContext:
    """Identifies one agent run and its on-disk locations."""

    run_id: str
    settings: Settings
    artifact_dir: Path
    log_path: Path
    workspace_dir: Path


def create_run_context(settings: Settings | None = None) -> RunContext:
    """Create a new run with UUID run_id and artifact/workspace directories."""
    settings = settings or get_settings()
    run_id = str(uuid.uuid4())
    artifact_dir = settings.artifacts_dir / run_id
    workspace_dir = settings.work_dir / run_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return RunContext(
        run_id=run_id,
        settings=settings,
        artifact_dir=artifact_dir,
        log_path=artifact_dir / "run.log",
        workspace_dir=workspace_dir,
    )
