import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import (
    ClosedIssueError,
    IssueContext,
    IssueFetchError,
    ensure_issue_open_or_forced,
    fetch_issue_context,
    fetch_issue_title,
    write_issue_context,
)
from go_agent.run_context import create_run_context

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_gh_json_fixture(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _load_fixture("issue_view.json")

    with patch("go_agent.github_issues.shutil.which", return_value="/usr/bin/gh"):
        with patch("go_agent.github_issues.subprocess.run") as run:
            run.return_value.stdout = json.dumps(payload)
            run.return_value.returncode = 0
            issue = fetch_issue_context("gin-gonic/gin", 42, Settings())

    assert issue.title == "Fix router panic on nil context"
    assert issue.state == "open"
    assert issue.labels == ["bug", "help wanted"]
    assert len(issue.comments) == 2
    assert issue.comments[0].author == "alice"


def test_comments_capped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _load_fixture("issue_view.json")
    payload["comments"] = [
        {
            "author": {"login": f"user{i}"},
            "body": f"comment {i}",
            "createdAt": f"2024-01-{i+1:02d}T00:00:00Z",
        }
        for i in range(30)
    ]

    with patch("go_agent.github_issues.shutil.which", return_value="/usr/bin/gh"):
        with patch("go_agent.github_issues.subprocess.run") as run:
            run.return_value.stdout = json.dumps(payload)
            run.return_value.returncode = 0
            issue = fetch_issue_context(
                "gin-gonic/gin",
                1,
                Settings(max_issue_comments=5),
            )

    assert len(issue.comments) == 5
    assert issue.comments[-1].author == "user29"


def test_closed_issue_raises_without_force():
    issue = IssueContext(
        repo="gin-gonic/gin",
        number=1,
        title="Done",
        state="closed",
    )
    logger = logging.getLogger("test")

    with pytest.raises(ClosedIssueError, match="closed"):
        ensure_issue_open_or_forced(issue, force=False, logger=logger)


def test_closed_issue_proceeds_with_force(caplog):
    issue = IssueContext(
        repo="gin-gonic/gin",
        number=1,
        title="Done",
        state="closed",
    )
    logger = logging.getLogger("test")

    with caplog.at_level(logging.WARNING):
        ensure_issue_open_or_forced(issue, force=True, logger=logger)

    assert "closed" in caplog.text.lower()
    assert "force" in caplog.text.lower()


def test_fetch_issue_title_delegates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = _load_fixture("issue_view.json")

    with patch("go_agent.github_issues.shutil.which", return_value="/usr/bin/gh"):
        with patch("go_agent.github_issues.subprocess.run") as run:
            run.return_value.stdout = json.dumps(payload)
            run.return_value.returncode = 0
            title = fetch_issue_title("gin-gonic/gin", 42, Settings())

    assert title == "Fix router panic on nil context"


def test_write_issue_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = Settings(artifacts_dir=tmp_path / "artifacts", work_dir=tmp_path / "workspaces")
    ctx = create_run_context(settings)
    issue = IssueContext(
        repo="gin-gonic/gin",
        number=7,
        title="Test",
        body="Body",
        labels=["bug"],
        state="open",
    )

    path = write_issue_context(ctx, issue)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["number"] == 7
    assert data["title"] == "Test"


def test_fetch_via_pygithub_when_no_gh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    sample = IssueContext(
        repo="gin-gonic/gin",
        number=2,
        title="From API",
        state="open",
    )

    with patch("go_agent.github_issues.shutil.which", return_value=None):
        with patch(
            "go_agent.github_issues._fetch_context_via_pygithub",
            return_value=sample,
        ):
            issue = fetch_issue_context("gin-gonic/gin", 2, Settings())

    assert issue.title == "From API"


def test_fetch_fails_without_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch("go_agent.github_issues.shutil.which", return_value=None):
        with patch("go_agent.github_issues._fetch_context_via_pygithub", return_value=None):
            with pytest.raises(IssueFetchError):
                fetch_issue_context("gin-gonic/gin", 1, Settings())
