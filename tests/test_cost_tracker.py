"""Tests for per-run LLM usage accounting."""

from __future__ import annotations

import json

from go_agent.cost_tracker import RunCostTracker, cost_tracking, get_current_tracker


def test_tracker_aggregates_stage_usage_and_writes_json(tmp_path):
    tracker = RunCostTracker(artifact_path=tmp_path / "usage.json")

    tracker.record(
        "plan",
        "gpt-test",
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        cost=0.001,
    )
    tracker.record(
        "plan",
        "gpt-test",
        {"input_tokens": 3, "output_tokens": 2},
        cost=0.002,
    )
    tracker.record_error("review", "gpt-strong")

    payload = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert payload["totals"] == {
        "input_tokens": 13,
        "output_tokens": 7,
        "total_tokens": 20,
        "calls": 2,
        "errors": 1,
        "estimated_cost_usd": 0.003,
    }
    assert payload["stages"][0]["stage"] == "plan"
    assert payload["stages"][0]["calls"] == 2
    assert payload["stages"][1]["stage"] == "review"
    assert payload["stages"][1]["errors"] == 1


def test_cost_tracking_context_sets_current_tracker(tmp_path):
    assert get_current_tracker() is None
    with cost_tracking(tmp_path) as tracker:
        assert get_current_tracker() is tracker
        tracker.record("code", "gpt-test", {"total_tokens": 4})
    assert get_current_tracker() is None
    payload = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert payload["totals"]["total_tokens"] == 4

