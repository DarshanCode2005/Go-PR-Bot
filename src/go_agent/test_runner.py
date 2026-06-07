"""Subprocess test runner — execute plan or skill test commands in the repo."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.planner import FixPlan
from go_agent.run_context import RunContext
from go_agent.skills import resolve_test_commands

_MAX_OUTPUT_CHARS = 65536


class TestRunError(RuntimeError):
    """Raised when test execution cannot complete (e.g. timeout)."""

    def __init__(self, message: str, *, result: TestRunResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class CommandResult(BaseModel):
    command: str
    exit_code: int
    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float


class TestRunResult(BaseModel):
    passed: bool
    commands: list[CommandResult] = Field(default_factory=list)
    resolved_commands: list[str] = Field(default_factory=list)
    source: Literal["plan", "skill", "merged"]
    plan_commands: list[str] = Field(default_factory=list)


def _truncate(text: str, limit: int = _MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + "\n... [truncated]"


def _coerce_output(data: str | bytes | None) -> str:
    if not data:
        return ""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data


def run_test_commands(
    repo_path: Path,
    commands: list[str],
    *,
    timeout: int,
    logger: logging.Logger | None = None,
    source: Literal["plan", "skill", "merged"] = "plan",
    plan_commands: list[str] | None = None,
) -> TestRunResult:
    """Run shell test commands in repo_path; pass only if all exit 0."""
    log = logger or logging.getLogger("go_agent")
    results: list[CommandResult] = []
    passed = True

    for command in commands:
        log.info("Running test command: %s", command)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=True,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            results.append(CommandResult(
                command=command,
                exit_code=-1,
                passed=False,
                stdout=_truncate(_coerce_output(exc.stdout)),
                stderr=_truncate(_coerce_output(exc.stderr)),
                duration_seconds=round(duration, 3),
            ))
            msg = f"test command timed out after {timeout}s: {command}"
            raise TestRunError(
                msg,
                result=TestRunResult(
                    passed=False,
                    commands=results,
                    resolved_commands=list(commands),
                    source=source,
                    plan_commands=list(plan_commands or commands),
                ),
            ) from exc

        duration = time.monotonic() - started
        cmd_passed = completed.returncode == 0
        result = CommandResult(
            command=command,
            exit_code=completed.returncode,
            passed=cmd_passed,
            stdout=_truncate(completed.stdout or ""),
            stderr=_truncate(completed.stderr or ""),
            duration_seconds=round(duration, 3),
        )
        results.append(result)
        if not cmd_passed:
            passed = False
            log.warning("Test command failed (exit %d): %s", completed.returncode, command)

    return TestRunResult(
        passed=passed,
        commands=results,
        resolved_commands=list(commands),
        source=source,
        plan_commands=list(plan_commands or commands),
    )


def run_tests(
    repo_path: Path,
    plan: FixPlan,
    repo: str,
    settings: Settings,
    logger: logging.Logger | None = None,
    *,
    iteration: int = 0,
    max_fix_iterations: int = 0,
) -> TestRunResult:
    """Resolve and run test commands for a fix plan."""
    commands, source = resolve_test_commands(
        plan,
        repo,
        iteration=iteration,
        max_fix_iterations=max_fix_iterations,
    )
    return run_test_commands(
        repo_path,
        commands,
        timeout=settings.test_timeout,
        logger=logger,
        source=source,
        plan_commands=list(plan.test_commands),
    )


def combined_output(result: TestRunResult) -> str:
    """Merge command outputs for state/logging."""
    parts: list[str] = []
    for item in result.commands:
        parts.append(f"$ {item.command}\n")
        if item.stdout:
            parts.append(item.stdout)
        if item.stderr:
            parts.append(item.stderr)
    return _truncate("\n".join(parts).strip())


def write_test_result(ctx: RunContext, result: TestRunResult) -> Path:
    """Write test_result.json under the run artifact directory."""
    path = ctx.artifact_dir / "test_result.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
