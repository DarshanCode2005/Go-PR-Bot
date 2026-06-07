"""Tests for planner agent structured fix plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from go_agent.config import Settings, clear_settings_cache
from go_agent.context_builder import ContextBundle, ContextFileEntry
from go_agent.github_issues import IssueContext
from go_agent.llm_client import set_completion_transport
from go_agent.planner import FixPlan, PlanError, build_fix_plan, enrich_fix_plan_payload, write_plan
from go_agent.run_context import create_run_context

VALID_PLAN_JSON = (
    '{"files":["context.go"],'
    '"steps":["Add nil guard in BindJSON"],'
    '"test_commands":["go test ./... -count=1"],'
    '"acceptance_criteria":["TestBindJSON handles nil context without panic"]}'
)


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


def _issue() -> IssueContext:
    body = (Path(__file__).parent / "fixtures" / "issue_bodies" / "gin_router.md").read_text(
        encoding="utf-8"
    )
    return IssueContext(
        repo="gin-gonic/gin",
        number=42,
        title="Panic in BindJSON when context is nil",
        body=body,
        state="open",
    )


def _bundle() -> ContextBundle:
    return ContextBundle(
        issue_number=42,
        repo="gin-gonic/gin",
        budget_chars=80000,
        total_chars=120,
        files=[
            ContextFileEntry(
                path="context.go",
                rationale="ripgrep hit for BindJSON",
                graph_distance=0,
                content_tier="snippet",
                content="func BindJSON() {}",
                char_count=20,
            )
        ],
    )


def test_fix_plan_model_validates_required_fields():
    with pytest.raises(ValidationError):
        FixPlan.model_validate(
            {
                "issue_number": 42,
                "repo": "gin-gonic/gin",
                "files": ["context.go"],
                "steps": [],
                "test_commands": ["go test ./... -count=1"],
                "acceptance_criteria": ["done"],
            }
        )
    with pytest.raises(ValidationError):
        FixPlan.model_validate(
            {
                "issue_number": 42,
                "repo": "gin-gonic/gin",
                "files": ["context.go"],
                "steps": ["fix"],
                "test_commands": ["echo hi"],
                "acceptance_criteria": ["done"],
            }
        )


def test_build_fix_plan_requires_api_key():
    issue = _issue()
    bundle = _bundle()
    settings = Settings(
        openai_api_key=None,
        anthropic_api_key=None,
        groq_api_key=None,
        gemini_api_key=None,
        xai_api_key=None,
    )
    with pytest.raises(PlanError, match="API key"):
        build_fix_plan(issue, bundle, ["BindJSON"], settings)


def test_build_fix_plan_parses_valid_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    set_completion_transport(RecordingTransport([VALID_PLAN_JSON]))
    plan = build_fix_plan(_issue(), _bundle(), ["BindJSON"], Settings())
    assert plan.files == ["context.go"]
    assert plan.steps
    assert any("go test" in command for command in plan.test_commands)


def test_build_fix_plan_retries_on_invalid_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport(["not json", VALID_PLAN_JSON])
    set_completion_transport(transport)
    plan = build_fix_plan(_issue(), _bundle(), ["BindJSON"], Settings())
    assert plan.files == ["context.go"]
    assert len(transport.calls) == 2


def test_build_fix_plan_raises_after_retry_exhausted(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    set_completion_transport(RecordingTransport(["not json", "still not json"]))
    with pytest.raises(PlanError, match="retry"):
        build_fix_plan(_issue(), _bundle(), ["BindJSON"], Settings())


def test_build_fix_plan_uses_strong_tier(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    transport = RecordingTransport([VALID_PLAN_JSON])
    set_completion_transport(transport)
    build_fix_plan(_issue(), _bundle(), ["BindJSON"], settings)
    assert transport.calls[0]["model"] == settings.model_strong


def test_write_plan_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    plan = FixPlan(
        issue_number=42,
        repo="gin-gonic/gin",
        files=["context.go"],
        steps=["Add nil guard"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["No panic on nil context"],
    )
    path = write_plan(ctx, plan)
    assert path == ctx.artifact_dir / "plan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["issue_number"] == 42
    assert payload["files"] == ["context.go"]


def test_fix_plan_file_dependencies_valid():
    plan = FixPlan(
        issue_number=42,
        repo="gin-gonic/gin",
        files=["pkg/foo.go", "pkg/bar.go"],
        steps=["Update foo then bar"],
        test_commands=["go test ./... -count=1"],
        acceptance_criteria=["Tests pass"],
        file_dependencies={"pkg/bar.go": ["pkg/foo.go"]},
    )
    assert plan.file_dependencies == {"pkg/bar.go": ["pkg/foo.go"]}


def test_fix_plan_rejects_unknown_dependency():
    with pytest.raises(ValidationError, match="unknown file"):
        FixPlan.model_validate(
            {
                "issue_number": 42,
                "repo": "gin-gonic/gin",
                "files": ["pkg/foo.go"],
                "steps": ["fix"],
                "test_commands": ["go test ./... -count=1"],
                "acceptance_criteria": ["done"],
                "file_dependencies": {"pkg/foo.go": ["pkg/missing.go"]},
            }
        )


def test_fix_plan_rejects_cycle():
    with pytest.raises(ValidationError, match="cycle"):
        FixPlan.model_validate(
            {
                "issue_number": 42,
                "repo": "gin-gonic/gin",
                "files": ["pkg/a.go", "pkg/b.go"],
                "steps": ["fix"],
                "test_commands": ["go test ./... -count=1"],
                "acceptance_criteria": ["done"],
                "file_dependencies": {
                    "pkg/a.go": ["pkg/b.go"],
                    "pkg/b.go": ["pkg/a.go"],
                },
            }
        )


def test_enrich_fix_plan_payload_strips_unknown_dependencies():
    payload = enrich_fix_plan_payload(
        {
            "files": ["baked_in.go"],
            "steps": ["Fix unix_addr"],
            "test_commands": ["go test -run TestUnixAddrValidation -count=1"],
            "acceptance_criteria": ["Tests pass"],
            "file_dependencies": {"validator.go": ["baked_in.go"], "baked_in.go": ["validator.go"]},
        },
        context_bundle=_bundle(),
    )
    plan = FixPlan.model_validate(
        {
            **payload,
            "issue_number": 32,
            "repo": "go-playground/validator",
        }
    )
    assert plan.files == ["baked_in.go"]
    assert plan.file_dependencies == {}


def test_enrich_fix_plan_payload_adds_test_file_from_bundle():
    bundle = ContextBundle(
        issue_number=32,
        repo="go-playground/validator",
        budget_chars=80000,
        total_chars=120,
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
    payload = enrich_fix_plan_payload(
        {
            "files": ["baked_in.go"],
            "steps": ["Fix unix_addr"],
            "test_commands": ["go test -run TestUnixAddrValidation -count=1"],
            "acceptance_criteria": ["Tests pass"],
        },
        context_bundle=bundle,
    )
    assert payload["files"] == ["baked_in.go", "validator_test.go"]


def test_build_fix_plan_sanitizes_invalid_dependencies_without_retry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    transport = RecordingTransport(
        [
            '{"files":["baked_in.go"],'
            '"steps":["Fix unix_addr"],'
            '"test_commands":["go test -run TestUnixAddrValidation -count=1"],'
            '"acceptance_criteria":["TestUnixAddrValidation passes"],'
            '"file_dependencies":{"validator.go":["baked_in.go"]}}'
        ]
    )
    set_completion_transport(transport)
    issue = IssueContext(
        repo="go-playground/validator",
        number=32,
        title="Adjust baked_in file dependencies",
        body="Reorder dependency metadata only.",
        state="open",
    )
    plan = build_fix_plan(issue, _bundle(), ["unix_addr"], Settings())
    assert plan.files == ["baked_in.go"]
    assert plan.file_dependencies == {}
    assert len(transport.calls) == 1
