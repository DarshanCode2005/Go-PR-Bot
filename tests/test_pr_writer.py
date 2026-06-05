"""Tests for PR title/body generation."""

from pathlib import Path
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import IssueContext
from go_agent.llm_client import set_completion_transport
from go_agent.pr_writer import (
    PRDraft,
    build_pr_draft,
    build_pr_template,
    render_pr_body,
    render_pr_markdown,
    write_pr_md,
)
from go_agent.run_context import create_run_context

FIXTURES = Path(__file__).parent / "fixtures" / "issue_bodies"

README_PATCH = """\
diff --git a/README.md b/README.md
index 0000000..1111111 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-v1
+v2
"""


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
        repo="gin-gonic/gin",
        number=42,
        title="Panic in BindJSON when context is nil",
        body=body,
        state="open",
    )


def test_template_pr_without_llm():
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings(openai_api_key=None, anthropic_api_key=None)
    draft = build_pr_draft(issue, settings, scope_hints=["context.go", "BindJSON"])
    rendered = render_pr_markdown(draft)

    assert issue.title in draft.problem
    assert "Fixes #42" in rendered
    assert "fixes #42" in draft.title.lower()


def test_template_includes_changed_files_from_patch():
    issue = _issue_from_fixture("gin_router.md")
    draft = build_pr_template(issue, patch_text=README_PATCH)
    assert "README.md" in draft.solution


def test_build_pr_draft_with_mocked_llm():
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings(openai_api_key="sk-test")
    enriched = PRDraft(
        title="fix: Guard nil context in BindJSON (fixes #42)",
        problem="Nil context causes panic in BindJSON.",
        solution="Add nil guard before binding JSON.",
        test_plan="- [ ] go test ./... -count=1\n- [ ] go test ./context/... -count=1",
        issue_number=42,
        repo="gin-gonic/gin",
    )
    with patch("go_agent.pr_writer.enrich_pr_llm", return_value=enriched):
        draft = build_pr_draft(issue, settings, scope_hints=["context.go"])
    assert draft.title == enriched.title
    assert "nil guard" in draft.solution.lower()


def test_build_pr_draft_with_mock_transport(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    issue = _issue_from_fixture("gin_router.md")
    settings = Settings()
    set_completion_transport(
        lambda **_: (
            '{"title":"fix: Guard nil context in BindJSON (fixes #42)",'
            '"problem":"Nil context causes panic in BindJSON.",'
            '"solution":"Add nil guard before binding JSON.",'
            '"test_plan":"- [ ] go test ./... -count=1"}'
        )
    )
    draft = build_pr_draft(issue, settings, scope_hints=["context.go"])
    assert "nil guard" in draft.solution.lower()


def test_render_pr_markdown_sections():
    draft = PRDraft(
        title="fix: Example (fixes #1)",
        problem="Something broke.",
        solution="Fix the bug.",
        test_plan="- [ ] go test ./... -count=1",
        issue_number=1,
        repo="owner/repo",
    )
    rendered = render_pr_markdown(draft)
    assert "## Problem" in rendered
    assert "## Solution" in rendered
    assert "## Test plan" in rendered
    assert "Fixes #1" in rendered


def test_render_pr_body_excludes_title():
    draft = PRDraft(
        title="fix: Example (fixes #1)",
        problem="Something broke.",
        solution="Fix the bug.",
        test_plan="- [ ] go test ./... -count=1",
        issue_number=1,
        repo="owner/repo",
    )
    body = render_pr_body(draft)
    assert not body.startswith("# ")
    assert "## Problem" in body
    assert "Fixes #1" in body
    assert render_pr_markdown(draft).startswith(f"# {draft.title}")


def test_write_pr_md_creates_artifact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = create_run_context()
    draft = build_pr_template(_issue_from_fixture("cobra_flags.md"), scope_hints=["command.go"])
    path = write_pr_md(ctx, draft)
    assert path == ctx.artifact_dir / "PR.md"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "## Problem" in content
    assert "Fixes #42" in content
