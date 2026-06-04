from typer.testing import CliRunner

from go_agent.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_main_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "version" in result.stdout
    assert "gin-gonic/gin" in result.stdout


def test_run_help():
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    for flag in ("--repo", "--issue", "--dry-run", "--create-pr"):
        assert flag in result.stdout


def test_run_missing_repo():
    result = runner.invoke(app, ["run", "--issue", "1"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "repo" in combined.lower() or "missing" in combined.lower()


def test_run_missing_issue():
    result = runner.invoke(app, ["run", "--repo", "gin-gonic/gin"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "issue" in combined.lower() or "missing" in combined.lower()


def test_run_invalid_repo_format():
    result = runner.invoke(app, ["run", "--repo", "bad", "--issue", "1"])
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "owner/name" in combined.lower() or "gin-gonic" in combined


def test_run_create_pr_conflicts_dry_run():
    result = runner.invoke(
        app,
        ["run", "--repo", "gin-gonic/gin", "--issue", "1", "--create-pr"],
    )
    assert result.exit_code == 2
    assert "no-dry-run" in (result.stdout + result.stderr).lower()


def test_run_not_implemented():
    result = runner.invoke(app, ["run", "--repo", "gin-gonic/gin", "--issue", "1"])
    assert result.exit_code == 1
    assert "not implemented" in (result.stdout + result.stderr).lower()
