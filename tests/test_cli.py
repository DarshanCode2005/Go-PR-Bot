from typer.testing import CliRunner

from go_agent.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.stdout


def test_run_not_implemented():
    result = runner.invoke(app, ["run", "--repo", "gin-gonic/gin", "--issue", "1"])
    assert result.exit_code == 1
