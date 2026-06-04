from go_agent.slug import issue_branch_name, slugify_issue_title


def test_slugify_basic():
    assert slugify_issue_title("Fix: HTTP 404 on /foo") == "fix-http-404-on-foo"


def test_slugify_whitespace_and_punctuation():
    assert slugify_issue_title("  Hello!!  ") == "hello"


def test_slugify_empty_fallback():
    assert slugify_issue_title("---") == "issue"


def test_slugify_truncates():
    long_title = "a" * 100
    assert len(slugify_issue_title(long_title, max_length=10)) == 10


def test_issue_branch_name():
    assert issue_branch_name(42, "Fix Router Bug") == "agent/issue-42-fix-router-bug"
