"""Per-run LLM token and cost usage tracking."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator


@dataclass
class StageUsage:
    stage: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    estimated_cost_usd: float | None = None
    errors: int = 0

    def record(self, *, input_tokens: int, output_tokens: int, total_tokens: int, cost: float | None) -> None:
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.total_tokens += total_tokens or input_tokens + output_tokens
        self.calls += 1
        if cost is not None:
            self.estimated_cost_usd = (self.estimated_cost_usd or 0.0) + cost

    def record_error(self) -> None:
        self.errors += 1

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": self.stage,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "errors": self.errors,
        }
        if self.estimated_cost_usd is not None:
            payload["estimated_cost_usd"] = round(self.estimated_cost_usd, 8)
        return payload


@dataclass
class RunCostTracker:
    artifact_path: Path | None = None
    stages: dict[tuple[str, str], StageUsage] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def record(self, stage: str, model: str, usage: Any, *, cost: float | None = None) -> None:
        input_tokens = _int_from_usage(usage, "prompt_tokens", "input_tokens")
        output_tokens = _int_from_usage(usage, "completion_tokens", "output_tokens")
        total_tokens = _int_from_usage(usage, "total_tokens")
        with self._lock:
            self._stage(stage, model).record(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cost=cost,
            )
            self.write()

    def record_error(self, stage: str, model: str) -> None:
        with self._lock:
            self._stage(stage, model).record_error()
            self.write()

    def to_dict(self) -> dict[str, Any]:
        rows = [item.to_dict() for item in self.stages.values()]
        rows.sort(key=lambda item: (item["stage"], item["model"]))
        total_cost = sum(item.estimated_cost_usd or 0.0 for item in self.stages.values())
        has_cost = any(item.estimated_cost_usd is not None for item in self.stages.values())
        return {
            "totals": {
                "input_tokens": sum(item.input_tokens for item in self.stages.values()),
                "output_tokens": sum(item.output_tokens for item in self.stages.values()),
                "total_tokens": sum(item.total_tokens for item in self.stages.values()),
                "calls": sum(item.calls for item in self.stages.values()),
                "errors": sum(item.errors for item in self.stages.values()),
                **({"estimated_cost_usd": round(total_cost, 8)} if has_cost else {}),
            },
            "stages": rows,
        }

    def write(self) -> Path | None:
        if self.artifact_path is None:
            return None
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        return self.artifact_path

    def _stage(self, stage: str, model: str) -> StageUsage:
        key = (stage, model)
        if key not in self.stages:
            self.stages[key] = StageUsage(stage=stage, model=model)
        return self.stages[key]

    @classmethod
    def from_file(cls, artifact_path: Path) -> RunCostTracker:
        tracker = cls(artifact_path=artifact_path)
        if not artifact_path.is_file():
            return tracker
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return tracker
        for item in payload.get("stages", []):
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage") or "unknown")
            model = str(item.get("model") or "unknown")
            usage = StageUsage(
                stage=stage,
                model=model,
                input_tokens=_int_from_usage(item, "input_tokens"),
                output_tokens=_int_from_usage(item, "output_tokens"),
                total_tokens=_int_from_usage(item, "total_tokens"),
                calls=_int_from_usage(item, "calls"),
                errors=_int_from_usage(item, "errors"),
            )
            cost = item.get("estimated_cost_usd")
            if cost is not None:
                try:
                    usage.estimated_cost_usd = float(cost)
                except (TypeError, ValueError):
                    usage.estimated_cost_usd = None
            tracker.stages[(stage, model)] = usage
        return tracker


_CURRENT_TRACKER: RunCostTracker | None = None


def get_current_tracker() -> RunCostTracker | None:
    return _CURRENT_TRACKER


@contextmanager
def cost_tracking(artifact_dir: Path) -> Iterator[RunCostTracker]:
    global _CURRENT_TRACKER
    previous = _CURRENT_TRACKER
    artifact_path = artifact_dir / "usage.json"
    tracker = previous or RunCostTracker.from_file(artifact_path)
    if tracker.artifact_path is None:
        tracker.artifact_path = artifact_path
    _CURRENT_TRACKER = tracker
    try:
        yield tracker
    finally:
        tracker.write()
        _CURRENT_TRACKER = previous


def _int_from_usage(usage: Any, *names: str) -> int:
    for name in names:
        value: Any = None
        if isinstance(usage, dict):
            value = usage.get(name)
        elif usage is not None:
            value = getattr(usage, name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0
