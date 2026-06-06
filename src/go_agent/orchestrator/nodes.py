"""LangGraph node functions — plan/code/integrate/test/lint wired; fix/review/pr partial."""

from __future__ import annotations

from go_agent.coder import build_proposed_patch, write_coder_artifact
from go_agent.config import get_settings
from go_agent.fixer import (
    build_corrective_patch,
    build_failure_context,
    write_fix_meta,
)
from go_agent.fixer import FixMeta
from go_agent.integrator import integrate_file_patches, write_integrator_artifact
from go_agent.lint_runner import LintRunError, combined_output as lint_combined_output
from go_agent.lint_runner import run_lints, write_lint_result
from go_agent.orchestrator.runtime import (
    branch_base_sha,
    bundle_from_state,
    coder_artifact_from_state,
    integration_base_sha,
    issue_from_state,
    logger_for_state,
    plan_from_state,
    repo_path_from_state,
    run_context_from_state,
)
from go_agent.orchestrator.state import AgentState, LintResult, ReviewResult, TestResult
from go_agent.patches import apply_patch_and_commit
from go_agent.planner import build_fix_plan, write_plan
from go_agent.test_runner import TestRunError, combined_output, run_tests, write_test_result


def plan_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    issue = issue_from_state(state)
    bundle = bundle_from_state(state)
    scope_hints = state.get("scope_hints") or []

    fix_plan = build_fix_plan(
        issue,
        bundle,
        scope_hints,
        settings,
        logger=logger,
    )
    write_plan(ctx, fix_plan)
    logger.info(
        "Fix plan: %d files, %d steps, %d test commands",
        len(fix_plan.files),
        len(fix_plan.steps),
        len(fix_plan.test_commands),
    )
    return {
        "status": "planning",
        "last_node": "plan",
        "fix_plan": fix_plan.model_dump(),
    }


def code_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    repo_path = repo_path_from_state(state)
    issue = issue_from_state(state)
    plan = plan_from_state(state)
    bundle = bundle_from_state(state)
    iteration = state.get("iteration", 0)

    if iteration > 0:
        logger.info("Using fix iteration %d patch from fix agent", iteration)
        return {
            "status": "coding",
            "last_node": "code",
        }

    artifact = build_proposed_patch(
        repo_path,
        issue,
        plan,
        bundle,
        settings,
        logger=logger,
    )
    write_coder_artifact(ctx, artifact)
    return {
        "status": "coding",
        "last_node": "code",
    }


def integrate_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    repo_path = repo_path_from_state(state)
    issue = issue_from_state(state)
    plan = plan_from_state(state)
    branch_base = branch_base_sha(state)
    integration_base = integration_base_sha(state, repo_path)
    iteration = state.get("iteration", 0)
    artifact = coder_artifact_from_state(state)

    result = integrate_file_patches(
        repo_path,
        artifact.files,
        plan,
        integration_base,
        settings,
        logger=logger,
    )
    write_integrator_artifact(ctx, result)
    patch_result = apply_patch_and_commit(
        repo_path,
        ctx,
        result.resolved_patch,
        issue.number,
        issue.title[:50],
        branch_base,
        logger,
        stack_on_head=iteration > 0,
    )
    logger.info(
        "Integrator patch applied; commit %s; changes at %s (iteration=%d)",
        patch_result.commit_sha[:8],
        patch_result.changes_patch_path,
        iteration,
    )
    return {
        "status": "integrating",
        "last_node": "integrate",
        "patch_applied": True,
        "changes_patch_path": str(patch_result.changes_patch_path),
        "commit_sha": patch_result.commit_sha,
        "commit_message": patch_result.commit_message,
    }


def test_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    repo_path = repo_path_from_state(state)
    plan = plan_from_state(state)
    issue = issue_from_state(state)

    try:
        result = run_tests(repo_path, plan, issue.repo, settings, logger=logger)
    except TestRunError as exc:
        if exc.result is not None:
            write_test_result(ctx, exc.result)
        raise
    write_test_result(ctx, result)
    output = combined_output(result)
    last_command = result.commands[-1] if result.commands else None
    first_failed = next((c for c in result.commands if not c.passed), None)
    test_result = TestResult(
        passed=result.passed,
        exit_code=first_failed.exit_code if first_failed else (last_command.exit_code if last_command else 0),
        output=output,
        command=result.resolved_commands[0] if result.resolved_commands else "",
        commands=result.resolved_commands,
        source=result.source,
    )
    logger.info(
        "Tests %s (%d command(s), source=%s, iteration=%d)",
        "passed" if result.passed else "failed",
        len(result.commands),
        result.source,
        state.get("iteration", 0),
    )
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": test_result.model_dump(),
    }


def lint_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    repo_path = repo_path_from_state(state)
    issue = issue_from_state(state)

    try:
        result = run_lints(repo_path, issue.repo, settings, logger=logger)
    except LintRunError as exc:
        if exc.result is not None:
            write_lint_result(ctx, exc.result)
        raise
    write_lint_result(ctx, result)
    output = lint_combined_output(result)
    last_command = result.commands[-1] if result.commands else None
    first_failed = next((c for c in result.commands if not c.passed), None)
    lint_result = LintResult(
        passed=result.passed,
        exit_code=first_failed.exit_code if first_failed else (last_command.exit_code if last_command else 0),
        output=output,
        command=result.resolved_commands[0] if result.resolved_commands else "",
        commands=result.resolved_commands,
        source=result.source,
        findings=[finding.model_dump() for finding in result.findings],
    )
    logger.info(
        "Lint %s (%d command(s), source=%s, %d finding(s), iteration=%d)",
        "passed" if result.passed else "failed",
        len(result.commands),
        result.source,
        len(result.findings),
        state.get("iteration", 0),
    )
    return {
        "status": "linting",
        "last_node": "lint",
        "lint_result": lint_result.model_dump(),
    }


def fix_node(state: AgentState) -> AgentState:
    ctx = run_context_from_state(state)
    settings = get_settings()
    logger = logger_for_state(state)
    repo_path = repo_path_from_state(state)
    issue = issue_from_state(state)
    plan = plan_from_state(state)
    bundle = bundle_from_state(state)

    fix_context = build_failure_context(state, max_iterations=settings.max_fix_iterations)
    logger.info(
        "Fix iteration %d/%d (source=%s)",
        fix_context.iteration,
        fix_context.max_iterations,
        fix_context.failure_source,
    )

    artifact = build_corrective_patch(
        repo_path,
        issue,
        plan,
        bundle,
        fix_context,
        settings,
        logger=logger,
    )
    write_coder_artifact(ctx, artifact)
    write_fix_meta(
        ctx,
        FixMeta(
            iteration=fix_context.iteration,
            max_iterations=fix_context.max_iterations,
            failure_source=fix_context.failure_source,
            error_summary=fix_context.test_output[:500] or fix_context.lint_output[:500],
            files=[item.path for item in artifact.files],
        ),
    )
    return {
        "status": "fixing",
        "last_node": "fix",
        "iteration": fix_context.iteration,
    }


def review_node(state: AgentState) -> AgentState:
    settings = get_settings()
    logger = logger_for_state(state)
    iteration = state.get("iteration", 0)
    test_result = state.get("test_result") or {}
    lint_result = state.get("lint_result") or {}
    test_passed = bool(test_result.get("passed"))
    lint_passed = bool(lint_result.get("passed")) if lint_result else True

    if test_passed and lint_passed:
        review = ReviewResult(approved=True, comments=["stub: approved"])
        logger.info("Review approved after iteration=%d", iteration)
        return {
            "status": "reviewing",
            "last_node": "review",
            "review": review.model_dump(),
        }

    cap = settings.max_fix_iterations
    if iteration >= cap:
        review = ReviewResult(
            approved=False,
            comments=[f"max fix iterations ({cap}) exceeded after iteration {iteration}"],
        )
        logger.error("Review failed: max fix iterations (%d) exceeded at iteration=%d", cap, iteration)
        return {
            "status": "failed",
            "last_node": "review",
            "review": review.model_dump(),
        }

    review = ReviewResult(
        approved=False,
        comments=["stub: validation still failing"],
    )
    return {
        "status": "failed",
        "last_node": "review",
        "review": review.model_dump(),
    }


def pr_node(state: AgentState) -> AgentState:
    logger = logger_for_state(state)
    status = state.get("status")
    iteration = state.get("iteration", 0)
    final_status = "done" if status != "failed" else "failed"
    logger.info("Run finished iteration=%d status=%s", iteration, final_status)
    return {
        "status": final_status,
        "last_node": "pr",
    }
