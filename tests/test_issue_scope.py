"""Tests for issue scope hint extraction."""

from pathlib import Path
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.issue_scope import (
    build_scope_hints,
    extract_scope_hints,
)
from go_agent.llm_client import set_completion_transport

FIXTURES = Path(__file__).parent / "fixtures" / "issue_bodies"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    set_completion_transport(None)
    yield
    set_completion_transport(None)
    clear_settings_cache()


def _issue_from_fixture(name: str) -> IssueContext:
    body = (FIXTURES / name).read_text(encoding="utf-8")
    return IssueContext(
        repo="owner/repo",
        number=1,
        title="Test issue",
        body=body,
        state="open",
    )


def _assert_contains(hints: list[str], needle: str) -> None:
    joined = " ".join(hints).lower()
    assert needle.lower() in joined, f"Expected {needle!r} in hints: {hints}"


def test_gin_router_fixture():
    hints = extract_scope_hints(_issue_from_fixture("gin_router.md"))
    _assert_contains(hints, "context.go")
    _assert_contains(hints, "BindJSON")
    _assert_contains(hints, "panic:")


def test_cobra_flags_fixture():
    hints = extract_scope_hints(_issue_from_fixture("cobra_flags.md"))
    _assert_contains(hints, "command.go")
    _assert_contains(hints, "PersistentFlags")
    _assert_contains(hints, "cmd")


def test_validator_error_fixture():
    hints = extract_scope_hints(_issue_from_fixture("validator_error.md"))
    _assert_contains(hints, "validator.go")
    _assert_contains(hints, "github.com/go-playground/validator/v10")
    _assert_contains(hints, "required")


def test_dedupes_and_orders():
    issue = IssueContext(
        repo="owner/repo",
        number=1,
        title="`foo.go` and `foo.go`",
        body="See `bar.go` then `bar.go` again.",
        state="open",
    )
    hints = extract_scope_hints(issue)
    assert hints == ["foo.go", "bar.go"]


def test_build_scope_hints_without_llm():
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings(openai_api_key=None, anthropic_api_key=None)
    hints = build_scope_hints(issue, settings)
    _assert_contains(hints, "context.go")
    _assert_contains(hints, "BindJSON")


def test_build_scope_hints_merges_llm_hints():
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings(openai_api_key="sk-test")
    with patch(
        "go_agent.issue_scope.enrich_scope_hints_llm",
        return_value=["context.go", "extra_symbol"],
    ):
        hints = build_scope_hints(issue, settings)
    assert "context.go" in hints
    assert "extra_symbol" in hints


def test_build_scope_hints_with_mock_transport(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings()

    set_completion_transport(
        lambda **_: '{"scope_hints": ["routergroup.go", "handleHTTPRequest"]}'
    )
    hints = build_scope_hints(issue, settings)

    assert "routergroup.go" in hints
    assert "handleHTTPRequest" in hints
