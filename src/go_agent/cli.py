"""CLI entry — implement full graph in orchestrator (see docs/GITHUB_ISSUES.md)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import typer

from go_agent.config import Settings, get_settings
from go_agent.constants import APPROVED_REPOS_HELP
from go_agent.logging_config import configure_run_logging
from go_agent.run_context import RunContext, create_run_context
from go_agent.branching import BranchError, BranchInfo, create_issue_branch, write_branch_meta
from go_agent.context_builder import (
    ContextBundle,
    ScopeBundle,
    build_context_bundle,
    build_scope_with_search,
    write_code_graph,
    write_context_bundle,
    write_scope_hints,
    write_search_hits,
)
from go_agent.github_issues import (
    ClosedIssueError,
    IssueContext,
    IssueFetchError,
    ensure_issue_open_or_forced,
    fetch_issue_context,
    write_issue_context,
)
from go_agent.github_pr import PRCreateError, maybe_create_pr
from go_agent.patches import PatchApplyError, apply_patch_and_commit
from go_agent.pr_writer import build_pr_draft, write_pr_md
from go_agent.repo_map import build_repo_map, write_repo_map
from go_agent.coder import CoderError
from go_agent.integrator import IntegratorError
from go_agent.orchestrator import (
    compile_graph,
    get_checkpointer,
    get_graph_state,
    graph_invoke_config,
    is_run_complete,
)
from go_agent.planner import PlanError
from go_agent.fixer import FixError
from go_agent.lint_runner import LintFinding, LintRunError, format_finding
from go_agent.test_runner import TestRunError
from go_agent.repo_rag import (
    build_rag_query,
    merge_search_hits,
    rag_hits_to_search_hits,
    retrieve_rag_hits,
    write_rag_hits,
)
from go_agent.run_meta import (
    RunMeta,
    RunMetaError,
    load_branch_info,
    load_context_bundle,
    load_issue_context,
    load_run_meta,
    load_scope_bundle,
    resolve_run_context,
    write_run_meta,
)
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


def _build_initial_graph_state(
    ctx: RunContext,
    repo: str,
    issue: int,
    repo_path: Path,
    scope_bundle: ScopeBundle,
    issue_ctx: IssueContext,
    context_bundle: ContextBundle,
    branch: BranchInfo,
) -> dict[str, Any]:
    return {
        "run_id": ctx.run_id,
        "repo": repo,
        "issue_number": issue,
        "artifact_dir": str(ctx.artifact_dir),
        "repo_path": str(repo_path),
        "scope_hints": scope_bundle.scope_hints,
        "issue_context": issue_ctx.model_dump(),
        "context_bundle": context_bundle.model_dump(),
        "branch_meta": {
            "base_sha": branch.base_sha,
            "branch_name": branch.branch_name,
        },
        "iteration": 0,
        "stop_after_integrate": True,
    }


def _compile_run_graph(meta: RunMeta | None, settings: Settings):
    checkpointer = get_checkpointer(settings)
    if meta is None:
        return compile_graph(
            include_test=True,
            include_closed_loop=True,
            checkpointer=checkpointer,
            settings=settings,
        )
    return compile_graph(
        include_test=meta.include_test,
        include_closed_loop=meta.include_closed_loop,
        checkpointer=checkpointer,
        settings=settings,
    )


def _invoke_graph(
    compiled: Any,
    state: dict[str, Any] | None,
    run_id: str,
    *,
    interrupt_after: list[str] | None = None,
) -> dict[str, Any]:
    config = graph_invoke_config(run_id)
    if interrupt_after:
        return compiled.invoke(state, config, interrupt_after=interrupt_after)
    return compiled.invoke(state, config)


def _handle_graph_exception(exc: Exception, logger: logging.Logger) -> None:
    if isinstance(exc, PlanError):
        logger.error("Planner failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, CoderError):
        logger.error("Coder failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, IntegratorError):
        logger.error("Integrator failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, PatchApplyError):
        logger.error("Integrator patch apply failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, TestRunError):
        logger.error("Test runner failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, LintRunError):
        logger.error("Lint runner failed: %s", exc)
        raise typer.Exit(code=1) from exc
    if isinstance(exc, FixError):
        logger.error("Fix agent failed: %s", exc)
        raise typer.Exit(code=1) from exc
    raise exc


def _finish_run(
    *,
    ctx: RunContext,
    logger: logging.Logger,
    settings: Settings,
    issue_ctx: IssueContext,
    scope_bundle: ScopeBundle,
    branch: BranchInfo,
    repo: str,
    repo_path: Path,
    final_state: dict[str, Any],
    patch_file: Path | None,
    dry_run: bool,
    create_pr: bool,
) -> None:
    patch_text: str | None = None
    commit_message: str | None = None
    tests_passed = True
    lint_passed = True

    if patch_file is None:
        changes_path = final_state.get("changes_patch_path")
        if changes_path:
            patch_text = Path(changes_path).read_text(encoding="utf-8")
        commit_message = final_state.get("commit_message")
        tests_passed = bool((final_state.get("test_result") or {}).get("passed"))
        lint_result = final_state.get("lint_result")
        if lint_result is not None:
            lint_passed = bool(lint_result.get("passed"))
    else:
        patch_text = patch_file.read_text(encoding="utf-8")
        result = apply_patch_and_commit(
            repo_path,
            ctx,
            patch_text,
            issue_ctx.number,
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

    pr_draft = build_pr_draft(
        issue_ctx,
        settings,
        scope_hints=scope_bundle.scope_hints,
        patch_text=patch_text,
        commit_message=commit_message,
    )
    pr_path = write_pr_md(ctx, pr_draft)
    logger.info("PR draft written to %s", pr_path)

    if patch_file is None and final_state.get("status") == "failed":
        review = final_state.get("review") or {}
        comments = review.get("comments") or []
        detail = comments[0] if comments else "validation failed after max fix iterations"
        logger.error(
            "Run failed after iteration=%d: %s",
            final_state.get("iteration", 0),
            detail,
        )
        raise typer.Exit(code=1)

    if patch_file is None and not tests_passed:
        logger.error("Tests failed; see %s/test_result.json", ctx.artifact_dir)
        raise typer.Exit(code=1)

    if patch_file is None and not lint_passed:
        lint_result = final_state.get("lint_result") or {}
        findings = lint_result.get("findings") or []
        if findings:
            sample = ", ".join(
                format_finding(LintFinding(**finding)) for finding in findings[:3]
            )
            logger.error(
                "Lint failed (%s); see %s/lint_result.json",
                sample,
                ctx.artifact_dir,
            )
        else:
            logger.error("Lint failed; see %s/lint_result.json", ctx.artifact_dir)
        raise typer.Exit(code=1)

    if not dry_run and create_pr:
        try:
            pr_result = maybe_create_pr(
                repo_path,
                repo,
                branch,
                pr_draft,
                ctx,
                logger,
            )
            typer.echo(pr_result.url)
            logger.info("Draft PR created: %s", pr_result.url)
            raise typer.Exit(code=0)
        except PRCreateError as exc:
            logger.error("%s", exc)
            raise typer.Exit(code=1) from exc

    logger.info(
        "Dry run complete: %s#%s artifacts at %s",
        repo,
        issue_ctx.number,
        ctx.artifact_dir,
    )
    raise typer.Exit(code=0)


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
    rag: bool = typer.Option(
        False,
        "--rag/--no-rag",
        help="Enable semantic RAG retrieval (requires pip install -e '.[rag]')",
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
    if rag:
        settings = settings.model_copy(update={"enable_rag": True})
    ctx = create_run_context(settings)
    logger = configure_run_logging(ctx)
    logger.info(
        "Starting run run_id=%s repo=%s issue=%s dry_run=%s create_pr=%s artifact_dir=%s",
        ctx.run_id,
        repo,
        issue,
        dry_run,
        create_pr,
        ctx.artifact_dir,
    )
    logger.info("Resume later with: go-agent resume --run-id %s", ctx.run_id)

    try:
        repo_path = ensure_repo_cloned(repo, ctx, logger)
    except RepoNotAllowedError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=2) from exc
    except CloneError as exc:
        logger.error("Clone failed: %s", exc)
        raise typer.Exit(code=1) from exc

    logger.info("Repository ready at %s", repo_path)

    repo_map = build_repo_map(repo_path, repo, settings)
    write_repo_map(ctx, repo_map)
    logger.info(
        "Repo map: module=%s packages=%d tree_depth=%d",
        repo_map.go_mod.module_path,
        len(repo_map.top_level_packages),
        repo_map.tree_depth,
    )

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
        scope_bundle, search_hits = build_scope_with_search(
            issue_ctx,
            repo_path,
            settings,
            logger=logger,
        )
        rag_query = build_rag_query(issue_ctx)
        rag_hits = retrieve_rag_hits(
            repo_path,
            issue_ctx,
            repo,
            settings,
            logger=logger,
        )
        if settings.enable_rag:
            write_rag_hits(ctx, issue_ctx, rag_query, rag_hits)
            search_hits = merge_search_hits(
                search_hits,
                rag_hits_to_search_hits(rag_hits),
            )
            logger.info("RAG search: %d hits merged", len(rag_hits))
        code_graph, context_bundle = build_context_bundle(
            repo_path,
            issue_ctx,
            scope_bundle,
            search_hits,
            settings,
        )
        write_scope_hints(ctx, scope_bundle)
        write_search_hits(ctx, scope_bundle, search_hits)
        write_code_graph(ctx, code_graph)
        write_context_bundle(ctx, context_bundle)
        logger.info(
            "Scope hints: %s",
            scope_bundle.scope_hints[:10],
        )
        logger.info(
            "Scope search: %d hits, %d files",
            len(search_hits),
            len(scope_bundle.files),
        )
        logger.info(
            "Context bundle: %d files, %d/%d chars",
            len(context_bundle.files),
            context_bundle.total_chars,
            context_bundle.budget_chars,
        )
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

    write_run_meta(
        ctx,
        RunMeta(
            run_id=ctx.run_id,
            repo=repo,
            issue_number=issue,
            dry_run=dry_run,
            create_pr=create_pr,
            force=force,
            enable_rag=settings.enable_rag,
            artifact_dir=str(ctx.artifact_dir),
            repo_path=str(repo_path),
            workspace_dir=str(ctx.workspace_dir),
            include_test=True,
            include_closed_loop=True,
            patch_file=str(patch_file) if patch_file is not None else None,
        ),
    )

    final_state: dict[str, Any] = {}
    if patch_file is None:
        try:
            compiled = _compile_run_graph(None, settings)
            initial_state = _build_initial_graph_state(
                ctx,
                repo,
                issue,
                repo_path,
                scope_bundle,
                issue_ctx,
                context_bundle,
                branch,
            )
            final_state = _invoke_graph(compiled, initial_state, ctx.run_id)
            logger.info(
                "Validation graph complete last_node=%s status=%s iteration=%d "
                "test_passed=%s lint_passed=%s",
                final_state.get("last_node"),
                final_state.get("status"),
                final_state.get("iteration", 0),
                (final_state.get("test_result") or {}).get("passed"),
                (final_state.get("lint_result") or {}).get("passed"),
            )
        except Exception as exc:
            _handle_graph_exception(exc, logger)

    try:
        _finish_run(
            ctx=ctx,
            logger=logger,
            settings=settings,
            issue_ctx=issue_ctx,
            scope_bundle=scope_bundle,
            branch=branch,
            repo=repo,
            repo_path=repo_path,
            final_state=final_state,
            patch_file=patch_file,
            dry_run=dry_run,
            create_pr=create_pr,
        )
    except PatchApplyError as exc:
        logger.error("Patch apply failed: %s", exc)
        raise typer.Exit(code=1) from exc


@app.command(
    epilog=_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
def resume(
    run_id: str = typer.Option(..., "--run-id", help="Run UUID from a prior go-agent run"),
    dry_run: bool | None = typer.Option(
        None,
        "--dry-run/--no-dry-run",
        help="Override dry-run from run metadata",
    ),
    create_pr: bool | None = typer.Option(
        None,
        "--create-pr/--no-create-pr",
        help="Override create-pr from run metadata",
    ),
) -> None:
    """Resume an interrupted run from the last LangGraph checkpoint.

    Example:

        go-agent resume --run-id 550e8400-e29b-41d4-a716-446655440000
    """
    settings = get_settings()
    try:
        meta = load_run_meta(run_id, settings)
        ctx = resolve_run_context(run_id, settings)
    except RunMetaError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    effective_dry_run = meta.dry_run if dry_run is None else dry_run
    effective_create_pr = meta.create_pr if create_pr is None else create_pr
    if effective_create_pr and effective_dry_run:
        typer.echo(
            "Error: --create-pr requires --no-dry-run.",
            err=True,
        )
        raise typer.Exit(code=2)

    logger = configure_run_logging(ctx)
    logger.info("Resuming run run_id=%s artifact_dir=%s", run_id, ctx.artifact_dir)

    repo_path = Path(meta.repo_path)
    if not repo_path.is_dir():
        logger.error("Workspace repo missing at %s", repo_path)
        raise typer.Exit(code=1)

    if meta.patch_file:
        logger.error("Run used --patch-file; graph resume is not supported")
        raise typer.Exit(code=2)

    try:
        issue_ctx = load_issue_context(ctx)
        branch = load_branch_info(ctx)
        scope_bundle = load_scope_bundle(ctx)
    except RunMetaError as exc:
        logger.error("%s", exc)
        raise typer.Exit(code=1) from exc

    compiled = _compile_run_graph(meta, settings)
    snapshot = get_graph_state(compiled, run_id)
    if is_run_complete(snapshot):
        logger.error("Run %s already complete (last_node=%s)", run_id, snapshot.values.get("last_node"))
        raise typer.Exit(code=2)

    if snapshot.values:
        invoke_state = None
        logger.info("Resuming run %s from %s", run_id, snapshot.next)
    else:
        try:
            context_bundle = load_context_bundle(ctx)
        except RunMetaError as exc:
            logger.error("%s", exc)
            raise typer.Exit(code=1) from exc
        invoke_state = _build_initial_graph_state(
            ctx,
            meta.repo,
            meta.issue_number,
            repo_path,
            scope_bundle,
            issue_ctx,
            context_bundle,
            branch,
        )
        logger.info("Resuming run %s from start (no checkpoint yet)", run_id)
    try:
        final_state = _invoke_graph(compiled, invoke_state, run_id)
        logger.info(
            "Resume complete last_node=%s status=%s iteration=%d",
            final_state.get("last_node"),
            final_state.get("status"),
            final_state.get("iteration", 0),
        )
    except Exception as exc:
        _handle_graph_exception(exc, logger)

    _finish_run(
        ctx=ctx,
        logger=logger,
        settings=settings,
        issue_ctx=issue_ctx,
        scope_bundle=scope_bundle,
        branch=branch,
        repo=meta.repo,
        repo_path=repo_path,
        final_state=final_state,
        patch_file=None,
        dry_run=effective_dry_run,
        create_pr=effective_create_pr,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
