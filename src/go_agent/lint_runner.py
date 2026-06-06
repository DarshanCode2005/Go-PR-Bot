"""Subprocess lint runner — go vet, optional golangci-lint, skill overrides."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from go_agent.config import Settings
from go_agent.run_context import RunContext
from go_agent.skills import resolve_lint_commands
from go_agent.test_runner import CommandResult, _coerce_output, _truncate

_GO_FILE_LINE = re.compile(
    r"^(?:\./)?(?P<file>[\w./-]+\.go):(?P<line>\d+)"
    r"(?:(?::(?P<col>\d+))?:(?:\s*(?P<message>.+))?)?",
    re.MULTILINE,
)


class LintRunError(RuntimeError):
    """Raised when lint execution cannot complete (e.g. timeout)."""

    def __init__(self, message: str, *, result: LintRunResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class LintFinding(BaseModel):
    file: str
    line: int
    column: int | None = None
    message: str = ""
    command: str = ""


class LintRunResult(BaseModel):
    passed: bool
    commands: list[CommandResult] = Field(default_factory=list)
    resolved_commands: list[str] = Field(default_factory=list)
    source: Literal["default", "skill_override"]
    findings: list[LintFinding] = Field(default_factory=list)


def parse_lint_findings(output: str, *, command: str = "") -> list[LintFinding]:
    """Extract file:line findings from go vet / golangci-lint output."""
    findings: list[LintFinding] = []
    seen: set[tuple[str, int, int | None, str]] = set()
    for match in _GO_FILE_LINE.finditer(output):
        col_raw = match.group("col")
        column = int(col_raw) if col_raw else None
        message = (match.group("message") or "").strip()
        key = (match.group("file"), int(match.group("line")), column, message)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            LintFinding(
                file=match.group("file"),
                line=int(match.group("line")),
                column=column,
                message=message,
                command=command,
            )
        )
    return findings


def _collect_findings(commands: list[CommandResult]) -> list[LintFinding]:
    findings: list[LintFinding] = []
    seen: set[tuple[str, int, int | None, str]] = set()
    for item in commands:
        if item.passed:
            continue
        output = "\n".join(part for part in (item.stdout, item.stderr) if part)
        for finding in parse_lint_findings(output, command=item.command):
            key = (finding.file, finding.line, finding.column, finding.message)
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    return findings


def run_lint_commands(
    repo_path: Path,
    commands: list[str],
    *,
    timeout: int,
    logger: logging.Logger | None = None,
    source: Literal["default", "skill_override"] = "default",
) -> LintRunResult:
    """Run shell lint commands in repo_path; pass only if all exit 0."""
    log = logger or logging.getLogger("go_agent")
    results: list[CommandResult] = []
    passed = True

    for command in commands:
        log.info("Running lint command: %s", command)
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
            partial = LintRunResult(
                passed=False,
                commands=results,
                resolved_commands=list(commands),
                source=source,
                findings=_collect_findings(results),
            )
            msg = f"lint command timed out after {timeout}s: {command}"
            raise LintRunError(msg, result=partial) from exc

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
            log.warning("Lint command failed (exit %d): %s", completed.returncode, command)

    return LintRunResult(
        passed=passed,
        commands=results,
        resolved_commands=list(commands),
        source=source,
        findings=_collect_findings(results),
    )


def run_lints(
    repo_path: Path,
    repo: str,
    settings: Settings,
    logger: logging.Logger | None = None,
) -> LintRunResult:
    """Resolve and run lint commands for a repo."""
    commands, source = resolve_lint_commands(repo, repo_path)
    return run_lint_commands(
        repo_path,
        commands,
        timeout=settings.lint_timeout,
        logger=logger,
        source=source,
    )


def combined_output(result: LintRunResult) -> str:
    """Merge command outputs for state/logging."""
    parts: list[str] = []
    for item in result.commands:
        parts.append(f"$ {item.command}\n")
        if item.stdout:
            parts.append(item.stdout)
        if item.stderr:
            parts.append(item.stderr)
    return _truncate("\n".join(parts).strip())


def format_finding(finding: LintFinding) -> str:
    """Human-readable file:line summary for logging."""
    location = f"{finding.file}:{finding.line}"
    if finding.column is not None:
        location = f"{location}:{finding.column}"
    if finding.message:
        return f"{location}: {finding.message}"
    return location


def write_lint_result(ctx: RunContext, result: LintRunResult) -> Path:
    """Write lint_result.json under the run artifact directory."""
    path = ctx.artifact_dir / "lint_result.json"
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
