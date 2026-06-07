"""Tests for planner test-awareness validation and prompt injection."""

from __future__ import annotations

from typing import Any

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.github_issues import IssueContext
from go_agent.llm_client import set_completion_transport
from go_agent.planner import (
    PlanError,
    _extract_known_tests,
    _issue_implies_behavior_change,
    _validate_test_awareness,
    build_fix_plan,
    build_planner_messages,
)
from go_agent.repo_search import SearchHit


class RecordingTransport:
    def __init__(self, responses: list[str | Exception]):
        self.responses = responses
        self.calls: list[dict[str, Any]] = []
        self.index = 0

    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> str:
        self.calls.append({"model": model, "messages": messages, "temperature": temperature})
        result = self.responses[min(self.index, len(self.responses) - 1)]
        self.index += 1
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture(autouse=True)
def _reset_state():
    clear_settings_cache()
    set_completion_transport(None)
    yield
    set_completion_transport(None)
    clear_settings_cache()


def _validator_issue() -> IssueContext:
    return IssueContext(
        repo="go-playground/validator",
        number=1348,
        title="unix_addr validation fails for empty string",
        body="The unix_addr tag incorrectly rejects empty strings.",
        state="open",
    )


def _doc_issue() -> IssueContext:
    return IssueContext(
        repo="example/docs",
        number=1,
        title="Update README formatting",
        body="Improve table layout in README.md.",
        state="open",
    )


def _validator_bundle() -> ContextBundle:
    return ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=200,
        files=[
            ContextFileEntry(
                path="baked_in.go",
                rationale="validation tag",
                graph_distance=0,
                content_tier="snippet",
                content="func isUnixAddr() {}",
                char_count=20,
            ),
            ContextFileEntry(
                path="validator_test.go",
                rationale="regression test",
                graph_distance=1,
                content_tier="snippet",
                content="func TestUnixAddrValidation(t *testing.T) {}",
                char_count=40,
            ),
        ],
    )


def test_issue_implies_behavior_change_for_validation_issue():
    assert _issue_implies_behavior_change(_validator_issue()) is True


def test_issue_implies_behavior_change_for_doc_issue():
    assert _issue_implies_behavior_change(_doc_issue()) is False


def test_extract_known_tests_from_bundle():
    known = _extract_known_tests(_validator_bundle())
    assert known == [("TestUnixAddrValidation", "validator_test.go")]


def test_extract_known_tests_from_search_hits():
    bundle = ContextBundle(
        issue_number=1348,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=20,
        files=[
            ContextFileEntry(
                path="baked_in.go",
                rationale="validation tag",
                graph_distance=0,
                content_tier="snippet",
                content="func isUnixAddr() {}",
                char_count=20,
            ),
        ],
    )
    hits = [
        SearchHit(
            path="validator_test.go",
            line_number=10,
            line_text="func TestUnixAddrValidation(t *testing.T) {",
            query="unix_addr",
        ),
    ]
    known = _extract_known_tests(bundle, hits)
    assert known == [("TestUnixAddrValidation", "validator_test.go")]


def test_validate_test_awareness_rejects_narrow_plan():
    payload = {
        "files": ["baked_in.go"],
        "steps": ["Fix unix_addr"],
        "test_commands": ["go test -run TestUnixAddrValidation -count=1"],
        "acceptance_criteria": ["Tests pass"],
    }
    with pytest.raises(PlanError, match="Behavior-change issue requires"):
        _validate_test_awareness(payload, _validator_issue(), context_bundle=_validator_bundle())


def test_validate_test_awareness_accepts_test_file():
    payload = {
        "files": ["baked_in.go", "validator_test.go"],
        "steps": ["Fix unix_addr"],
        "test_commands": ["go test -count=1"],
        "acceptance_criteria": ["Tests pass"],
    }
    _validate_test_awareness(payload, _validator_issue(), context_bundle=_validator_bundle())


def test_validate_test_awareness_accepts_test_named_criteria():
    payload = {
        "files": ["baked_in.go"],
        "steps": ["Fix unix_addr"],
        "test_commands": ["go test -count=1"],
        "acceptance_criteria": ["TestUnixAddrValidation passes"],
    }
    _validate_test_awareness(payload, _validator_issue(), context_bundle=_validator_bundle())


def test_validate_test_awareness_skips_doc_issue():
    payload = {
        "files": ["README.md"],
        "steps": ["Fix table"],
        "test_commands": ["go test ./... -count=1"],
        "acceptance_criteria": ["README updated"],
    }
    _validate_test_awareness(payload, _doc_issue(), context_bundle=_validator_bundle())


def test_build_planner_messages_includes_known_tests():
    messages = build_planner_messages(_validator_issue(), _validator_bundle(), ["unix_addr"])
    user = messages[-1]["content"]
    assert "Known tests in context:" in user
    assert "TestUnixAddrValidation (validator_test.go)" in user


def test_build_planner_messages_includes_test_awareness_skill():
    messages = build_planner_messages(_validator_issue(), _validator_bundle(), ["unix_addr"])
    user = messages[-1]["content"]
    assert "Stage skill notes:" in user
    assert "Read failing or related" in user


NARROW_PLAN_JSON = (
    '{"files":["baked_in.go"],'
    '"steps":["Fix unix_addr"],'
    '"test_commands":["go test -run TestUnixAddrValidation -count=1"],'
    '"acceptance_criteria":["Tests pass"]}'
)

TEST_AWARE_PLAN_JSON = (
    '{"files":["baked_in.go","validator_test.go"],'
    '"steps":["Read TestUnixAddrValidation expectations","Fix unix_addr in baked_in.go"],'
    '"test_commands":["go test -run TestUnixAddrValidation -count=1"],'
    '"acceptance_criteria":["TestUnixAddrValidation passes"]}'
)


def test_build_fix_plan_retries_on_test_unaware_plan(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport([NARROW_PLAN_JSON, TEST_AWARE_PLAN_JSON])
    set_completion_transport(transport)
    plan = build_fix_plan(
        _validator_issue(),
        _validator_bundle(),
        ["unix_addr"],
        Settings(),
    )
    assert "validator_test.go" in plan.files
    assert len(transport.calls) == 2
    retry_user = transport.calls[1]["messages"][-1]["content"]
    assert "TestUnixAddrValidation" in retry_user


def test_validator_1348_regression_fixture(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport([NARROW_PLAN_JSON, TEST_AWARE_PLAN_JSON])
    set_completion_transport(transport)
    plan = build_fix_plan(
        _validator_issue(),
        _validator_bundle(),
        ["unix_addr"],
        Settings(),
    )
    has_test_file = any(path.endswith("_test.go") for path in plan.files)
    has_named_test = any("TestUnixAddrValidation" in item for item in plan.acceptance_criteria)
    assert has_test_file or has_named_test
    assert has_named_test or any("TestUnixAddrValidation" in step for step in plan.steps)
