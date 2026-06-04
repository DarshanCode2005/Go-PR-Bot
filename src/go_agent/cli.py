"""CLI entry — implement full graph in orchestrator (see docs/GITHUB_ISSUES.md)."""

from __future__ import annotations

import typer

app = typer.Typer(help="Agentic AI contributor for approved Go OSS projects.")


@app.command()
def version() -> None:
    """Print package version."""
    from go_agent import __version__

    typer.echo(__version__)


@app.command()
def run(
    repo: str = typer.Option(..., help="owner/name e.g. gin-gonic/gin"),
    issue: int = typer.Option(..., help="GitHub issue number"),
    dry_run: bool = typer.Option(True, help="No git push / PR create"),
    create_pr: bool = typer.Option(False, help="Open draft PR via gh"),
) -> None:
    """Run the agent pipeline on a GitHub issue."""
    typer.echo(f"Not implemented yet: {repo}#{issue} dry_run={dry_run} create_pr={create_pr}")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
