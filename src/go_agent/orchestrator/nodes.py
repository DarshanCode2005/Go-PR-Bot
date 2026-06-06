"""LangGraph node functions — plan/code/integrate wired; test/fix/review/pr stubbed."""

from __future__ import annotations

from go_agent.coder import build_proposed_patch, write_coder_artifact
from go_agent.config import get_settings
from go_agent.integrator import integrate_file_patches, write_integrator_artifact
from go_agent.orchestrator.runtime import (
    branch_base_sha,
    bundle_from_state,
    coder_artifact_from_state,
    issue_from_state,
    logger_for_state,
    plan_from_state,
    repo_path_from_state,
    run_context_from_state,
)
from go_agent.orchestrator.state import AgentState, ReviewResult, TestResult
from go_agent.patches import apply_patch_and_commit
from go_agent.planner import build_fix_plan, write_plan
from go_agent.test_runner import combined_output, run_tests, write_test_result


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
    base_sha = branch_base_sha(state)
    artifact = coder_artifact_from_state(state)

    result = integrate_file_patches(
        repo_path,
        artifact.files,
        plan,
        base_sha,
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
        base_sha,
        logger,
    )
    logger.info(
        "Integrator patch applied; commit %s; changes at %s",
        patch_result.commit_sha[:8],
        patch_result.changes_patch_path,
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

    result = run_tests(repo_path, plan, issue.repo, settings, logger=logger)
    write_test_result(ctx, result)
    output = combined_output(result)
    last_command = result.commands[-1] if result.commands else None
    test_result = TestResult(
        passed=result.passed,
        exit_code=last_command.exit_code if last_command else 1,
        output=output,
        command=result.resolved_commands[0] if result.resolved_commands else "",
        commands=result.resolved_commands,
        source=result.source,
    )
    logger.info(
        "Tests %s (%d command(s), source=%s)",
        "passed" if result.passed else "failed",
        len(result.commands),
        result.source,
    )
    return {
        "status": "testing",
        "last_node": "test",
        "test_result": test_result.model_dump(),
    }


def fix_node(state: AgentState) -> AgentState:
    iteration = state.get("iteration", 0) + 1
    return {
        "status": "fixing",
        "last_node": "fix",
        "iteration": iteration,
    }


def review_node(state: AgentState) -> AgentState:
    test_result = state.get("test_result") or {}
    passed = bool(test_result.get("passed"))
    if passed:
        review = ReviewResult(approved=True, comments=["stub: approved"])
        return {
            "status": "reviewing",
            "last_node": "review",
            "review": review.model_dump(),
        }
    review = ReviewResult(
        approved=False,
        comments=["stub: tests still failing after max iterations"],
    )
    return {
        "status": "failed",
        "last_node": "review",
        "review": review.model_dump(),
    }


def pr_node(state: AgentState) -> AgentState:
    status = state.get("status")
    final_status = "done" if status != "failed" else "failed"
    return {
        "status": final_status,
        "last_node": "pr",
    }
