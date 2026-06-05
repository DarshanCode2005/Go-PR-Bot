"""CLI entry — implement full graph in orchestrator (see docs/GITHUB_ISSUES.md)."""

from __future__ import annotations

import re
from pathlib import Path

import typer

from go_agent.config import get_settings
from go_agent.constants import APPROVED_REPOS_HELP
from go_agent.logging_config import configure_run_logging
from go_agent.run_context import create_run_context
from go_agent.branching import BranchError, create_issue_branch, write_branch_meta
from go_agent.context_builder import prepare_scope, write_scope_hints
from go_agent.github_issues import (
    ClosedIssueError,
    IssueFetchError,
    ensure_issue_open_or_forced,
    fetch_issue_context,
    write_issue_context,
)
from go_agent.patches import PatchApplyError, apply_patch_and_commit
from go_agent.pr_writer import build_pr_draft, write_pr_md
from go_agent.workspace import CloneError, RepoNotAllowedError, assert_repo_allowed, ensure_repo_cloned

_REPO_PATTERN = re.compile(r"^[\w.-]+/[\w.-]+$")

_EPILOG = f"Approved repos: {APPROVED_REPOS_HELP}"

app = typer.Typer(
    help="Agentic AI contributor for approved Go OSS projects.",
    no_args_is_help=True,
    epilog=_EPILOG,
)


def _validate_repo(repo: str) -> str:
    if not _REPO_PATTERN.match(repo):
        raise typer.BadParameter(
            "expected owner/name, e.g. gin-gonic/gin",
            param_hint="--repo",
        )
    try:
        assert_repo_allowed(repo)
    except RepoNotAllowedError as exc:
        raise typer.BadParameter(str(exc), param_hint="--repo") from exc
    return repo


@app.command()
def version() -> None:
    """Print package version."""
    from go_agent import __version__

    typer.echo(__version__)


@app.command(
    epilog=_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
def run(
    repo: str = typer.Option(
        ...,
        "--repo",
        help=f"GitHub owner/name; approved: {APPROVED_REPOS_HELP}",
        callback=_validate_repo,
    ),
    issue: int = typer.Option(..., "--issue", help="GitHub issue number"),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Skip git push and PR creation (default: true)",
    ),
    create_pr: bool = typer.Option(
        False,
        "--create-pr",
        help="Open draft PR via gh (requires --no-dry-run)",
    ),
    patch_file: Path | None = typer.Option(
        None,
        "--patch-file",
        help="Apply unified diff from file and commit (dev/testing)",
        exists=True,
        readable=True,
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Proceed when the GitHub issue is closed",
    ),
) -> None:
    """Run the agent pipeline on a GitHub issue.

    Example:

        go-agent run --repo gin-gonic/gin --issue 1234 --dry-run
    """
    if create_pr and dry_run:
        typer.echo(
            "Error: --create-pr requires --no-dry-run.",
            err=True,
        )
        raise typer.Exit(code=2)

    settings = get_settings()
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)
    logger.info(
        "Starting run repo=%s issue=%s dry_run=%s create_pr=%s artifact_dir=%s",
        repo,
        issue,
        dry_run,
        create_pr,
        ctx.artifact_dir,
    )

    try:
        repo_path = ensure_repo_cloned(repo, ctx, logger)
    except RepoNotAllowedError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=2) from exc
    except CloneError as exc:
        logger.error("Clone failed: %s", exc)
        raise typer.Exit(code=1) from exc

    logger.info("Repository ready at %s", repo_path)

    try:
        issue_ctx = fetch_issue_context(repo, issue, settings)
        ensure_issue_open_or_forced(issue_ctx, force=force, logger=logger)
        write_issue_context(ctx, issue_ctx)
        logger.info(
            "Issue #%s state=%s labels=%s comments=%d",
            issue_ctx.number,
            issue_ctx.state,
            issue_ctx.labels,
            len(issue_ctx.comments),
        )
        scope_bundle = prepare_scope(issue_ctx, settings)
        write_scope_hints(ctx, scope_bundle)
        logger.info("Scope hints: %s", scope_bundle.scope_hints[:10])
        branch = create_issue_branch(repo_path, issue, issue_ctx.title, logger)
        write_branch_meta(ctx, branch)
        logger.info(
            "Branch %s at base %s (default %s)",
            branch.branch_name,
            branch.base_sha[:8],
            branch.default_branch,
        )
    except ClosedIssueError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=2) from exc
    except IssueFetchError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=1) from exc
    except BranchError as exc:
        logger.error("Branch creation failed: %s", exc)
        raise typer.Exit(code=1) from exc

    patch_text: str | None = None
    commit_message: str | None = None
    if patch_file is not None:
        try:
            patch_text = patch_file.read_text(encoding="utf-8")
            result = apply_patch_and_commit(
                repo_path,
                ctx,
                patch_text,
                issue,
                issue_ctx.title[:50],
                branch.base_sha,
                logger,
            )
            patch_text = result.changes_patch_path.read_text(encoding="utf-8")
            commit_message = result.commit_message
            logger.info(
                "Patch applied; commit %s; changes at %s",
                result.commit_sha[:8],
                result.changes_patch_path,
            )
        except PatchApplyError as exc:
            logger.error("Patch apply failed: %s", exc)
            raise typer.Exit(code=1) from exc

    pr_draft = build_pr_draft(
        issue_ctx,
        settings,
        scope_hints=scope_bundle.scope_hints,
        patch_text=patch_text,
        commit_message=commit_message,
    )
    pr_path = write_pr_md(ctx, pr_draft)
    logger.info("PR draft written to %s", pr_path)

    logger.warning(
        "Pipeline not implemented yet: %s#%s dry_run=%s create_pr=%s",
        repo,
        issue,
        dry_run,
        create_pr,
    )
    raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
