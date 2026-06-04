from unittest.mock import patch

import pytest

from go_agent.config import Settings, clear_settings_cache
from go_agent.github_issues import IssueFetchError, fetch_issue_title


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_fetch_via_gh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch("go_agent.github_issues.shutil.which", return_value="/usr/bin/gh"):
        with patch("go_agent.github_issues.subprocess.run") as run:
            run.return_value.stdout = "Fix the bug\n"
            run.return_value.returncode = 0
            title = fetch_issue_title("gin-gonic/gin", 1, Settings())
    assert title == "Fix the bug"


def test_fetch_via_pygithub_when_no_gh(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    with patch("go_agent.github_issues.shutil.which", return_value=None):
        with patch("go_agent.github_issues._fetch_via_pygithub", return_value="From API"):
            title = fetch_issue_title("gin-gonic/gin", 2, Settings())
    assert title == "From API"


def test_fetch_fails_without_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with patch("go_agent.github_issues.shutil.which", return_value=None):
        with patch("go_agent.github_issues._fetch_via_pygithub", return_value=None):
            with pytest.raises(IssueFetchError):
                fetch_issue_title("gin-gonic/gin", 1, Settings())
