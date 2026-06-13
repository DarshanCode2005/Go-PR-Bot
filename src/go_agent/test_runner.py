"""Subprocess test runner — execute plan or skill test commands in the repo."""

from __future__ import annotations

import logging
import re
import shlex
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
_SHELL_METACHAR_RE = re.compile(r"[;|`]|&&|\$\(")
_FAIL_TEST_LINE = re.compile(r"--- FAIL:\s+(\w+)")
_FAIL_PACKAGE_LINE = re.compile(r"^FAIL\t(\S+)")


class TestRunError(RuntimeError):
    """Raised when test execution cannot complete (e.g. timeout)."""

    def __init__(self, message: str, *, result: TestRunResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class CommandResult(BaseModel):
    command: str
    argv: list[str] = Field(default_factory=list)
    exit_code: int
    passed: bool
    stdout: str
    stderr: str
    duration_seconds: float


class TestRunResult(BaseModel):
    passed: bool
    commands: list[CommandResult] = Field(default_factory=list)
    resolved_commands: list[str] = Field(default_factory=list)
    command_argv: list[list[str]] = Field(default_factory=list)
    source: Literal["plan", "skill", "merged"]
    plan_commands: list[str] = Field(default_factory=list)
    mode: Literal["full", "scoped", "scoped_then_full"] = "full"
    scoped_from_failure: bool = False


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


def _argv_display(argv: list[str], *, cwd: Path | None = None) -> str:
    text = shlex.join(argv)
    if cwd is not None:
        return f"(cd {cwd}) {text}"
    return text


def tokenize_test_command(command: str) -> list[str]:
    """Split a test command into argv; reject shell metacharacters."""
    if _SHELL_METACHAR_RE.search(command):
        msg = f"unsafe shell metacharacters in test command: {command!r}"
        raise TestRunError(msg)
    return shlex.split(command)


def _go_test_flags_from_command(command: str) -> list[str]:
    """Extract go test flags from a command, excluding -run and package args."""
    try:
        tokens = tokenize_test_command(command)
    except TestRunError:
        return ["-count=1"]
    if len(tokens) < 2 or tokens[0] != "go" or tokens[1] != "test":
        return ["-count=1"]
    flags: list[str] = []
    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "-run":
            index += 2
            continue
        if token.startswith("-"):
            flags.append(token)
            if "=" not in token and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                next_token = tokens[index + 1]
                if next_token not in {".", "./..."} and not next_token.startswith("./"):
                    flags.append(next_token)
                    index += 2
                    continue
            index += 1
            continue
        index += 1
    return flags or ["-count=1"]


def _run_pattern(test_names: list[str]) -> str:
    if len(test_names) == 1:
        return f"^{test_names[0]}$"
    inner = "|".join(test_names)
    return f"^({inner})$"


def _pair_tests_to_packages(test_output: str) -> dict[str, list[str]]:
    """Map import paths to failing test names by walking go test output."""
    from go_agent.fixer import parse_failing_packages, parse_failing_tests

    packages: dict[str, list[str]] = {}
    pending_tests: list[str] = []

    for line in test_output.splitlines():
        test_match = _FAIL_TEST_LINE.search(line)
        if test_match:
            pending_tests.append(test_match.group(1))
            continue
        pkg_match = _FAIL_PACKAGE_LINE.match(line)
        if pkg_match and pending_tests:
            import_path = pkg_match.group(1)
            bucket = packages.setdefault(import_path, [])
            for name in pending_tests:
                if name not in bucket:
                    bucket.append(name)
            pending_tests = []

    if pending_tests:
        all_packages = parse_failing_packages(test_output)
        if len(all_packages) == 1:
            bucket = packages.setdefault(all_packages[0], [])
            for name in pending_tests:
                if name not in bucket:
                    bucket.append(name)
        elif not packages:
            failing = parse_failing_tests(test_output)
            if failing:
                packages[""] = list(failing)

    return packages


def _resolve_package_dir(repo_path: Path, import_path: str) -> Path | None:
    if not import_path:
        return repo_path
    try:
        completed = subprocess.run(
            ["go", "list", "-f", "{{.Dir}}", import_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    dir_text = completed.stdout.strip()
    if not dir_text:
        return None
    return Path(dir_text)


def derive_compile_check_commands(
    test_output: str,
    repo_path: Path,
) -> list[tuple[list[str], Path]]:
    """Derive fast go build commands when failures are compile-time, not assertions."""
    from go_agent.failure_parse import is_compile_failure, parse_compile_error_files

    if not is_compile_failure(test_output):
        return []

    error_files = parse_compile_error_files(test_output)
    package_dirs: list[Path] = []
    seen_dirs: set[Path] = set()

    for path in error_files:
        package_dir = (repo_path / path).parent.resolve()
        if package_dir.is_dir() and package_dir not in seen_dirs:
            seen_dirs.add(package_dir)
            package_dirs.append(package_dir)

    if not package_dirs:
        return [(["go", "build", "./..."], repo_path)]

    return [(["go", "build", "./..."], package_dir) for package_dir in package_dirs]


def derive_scoped_test_commands(
    test_output: str,
    repo_path: Path,
    *,
    base_commands: list[str] | None = None,
) -> list[tuple[list[str], Path]]:
    """Derive scoped go test argv commands and working directories from failure output."""
    packages = _pair_tests_to_packages(test_output)
    if not packages:
        return []

    base = (base_commands or ["go test -count=1 ./..."])[0]
    flags = _go_test_flags_from_command(base)
    derived: list[tuple[list[str], Path]] = []

    for import_path, test_names in packages.items():
        if not test_names:
            continue
        package_dir = _resolve_package_dir(repo_path, import_path)
        if package_dir is None:
            return []
        argv = ["go", "test", *flags, "-run", _run_pattern(test_names), "."]
        derived.append((argv, package_dir))

    return derived


def _normalize_commands(
    commands: list[str] | list[list[str]],
) -> list[tuple[list[str], Path | None]]:
    """Normalize string or argv command lists into (argv, cwd) pairs."""
    if not commands:
        return []
    if isinstance(commands[0], str):
        return [(tokenize_test_command(str(item)), None) for item in commands]
    return [(list(item), None) for item in commands]


def run_test_commands(
    repo_path: Path,
    commands: list[str] | list[list[str]],
    *,
    timeout: int,
    logger: logging.Logger | None = None,
    source: Literal["plan", "skill", "merged"] = "plan",
    plan_commands: list[str] | None = None,
    command_cwds: list[Path | None] | None = None,
    mode: Literal["full", "scoped", "scoped_then_full"] = "full",
    scoped_from_failure: bool = False,
    phase_labels: list[str] | None = None,
) -> TestRunResult:
    """Run test commands as argv arrays in repo_path; pass only if all exit 0."""
    log = logger or logging.getLogger("go_agent")
    specs = _normalize_commands(commands)
    if command_cwds is not None:
        if len(command_cwds) != len(specs):
            msg = "command_cwds length must match commands length"
            raise TestRunError(msg)
        specs = [(argv, command_cwds[index]) for index, (argv, _) in enumerate(specs)]

    results: list[CommandResult] = []
    argv_list: list[list[str]] = []
    resolved: list[str] = []
    passed = True

    for index, (argv, cwd_override) in enumerate(specs):
        cwd = cwd_override or repo_path
        display = _argv_display(argv, cwd=cwd if cwd_override else None)
        phase = phase_labels[index] if phase_labels and index < len(phase_labels) else None
        if phase:
            display = f"[{phase}] {display}"
        log.info("Running test command: %s", display)
        argv_list.append(argv)
        resolved.append(display)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - started
            results.append(CommandResult(
                command=display,
                argv=argv,
                exit_code=-1,
                passed=False,
                stdout=_truncate(_coerce_output(exc.stdout)),
                stderr=_truncate(_coerce_output(exc.stderr)),
                duration_seconds=round(duration, 3),
            ))
            msg = f"test command timed out after {timeout}s: {display}"
            raise TestRunError(
                msg,
                result=TestRunResult(
                    passed=False,
                    commands=results,
                    resolved_commands=resolved,
                    command_argv=argv_list,
                    source=source,
                    plan_commands=list(plan_commands or []),
                    mode=mode,
                    scoped_from_failure=scoped_from_failure,
                ),
            ) from exc

        duration = time.monotonic() - started
        cmd_passed = completed.returncode == 0
        result = CommandResult(
            command=display,
            argv=argv,
            exit_code=completed.returncode,
            passed=cmd_passed,
            stdout=_truncate(completed.stdout or ""),
            stderr=_truncate(completed.stderr or ""),
            duration_seconds=round(duration, 3),
        )
        results.append(result)
        if not cmd_passed:
            passed = False
            log.warning("Test command failed (exit %d): %s", completed.returncode, display)

    return TestRunResult(
        passed=passed,
        commands=results,
        resolved_commands=resolved,
        command_argv=argv_list,
        source=source,
        plan_commands=list(plan_commands or []),
        mode=mode,
        scoped_from_failure=scoped_from_failure,
    )


def _resolve_effective_mode(
    *,
    mode: Literal["auto", "scoped", "full"],
    settings: Settings,
    iteration: int,
    prior_test_output: str | None,
) -> Literal["scoped", "full"]:
    if mode == "full":
        return "full"
    if mode == "scoped":
        return "scoped"
    if not settings.scoped_test_enabled:
        return "full"
    if iteration == 0 or not prior_test_output:
        return "full"
    return "scoped"


def _merge_results(
    scoped: TestRunResult,
    full: TestRunResult,
) -> TestRunResult:
    return TestRunResult(
        passed=scoped.passed and full.passed,
        commands=[*scoped.commands, *full.commands],
        resolved_commands=[*scoped.resolved_commands, *full.resolved_commands],
        command_argv=[*scoped.command_argv, *full.command_argv],
        source=full.source,
        plan_commands=full.plan_commands,
        mode="scoped_then_full",
        scoped_from_failure=True,
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
    mode: Literal["auto", "scoped", "full"] = "auto",
    prior_test_output: str | None = None,
) -> TestRunResult:
    """Resolve and run test commands for a fix plan."""
    full_commands, source = resolve_test_commands(
        plan,
        repo,
        iteration=iteration,
        max_fix_iterations=max_fix_iterations,
    )
    plan_commands = list(plan.test_commands)
    effective_mode = _resolve_effective_mode(
        mode=mode,
        settings=settings,
        iteration=iteration,
        prior_test_output=prior_test_output,
    )

    if effective_mode == "full":
        return run_test_commands(
            repo_path,
            full_commands,
            timeout=settings.test_timeout,
            logger=logger,
            source=source,
            plan_commands=plan_commands,
            mode="full",
        )

    prior_output = prior_test_output or ""
    compile_checks = derive_compile_check_commands(prior_output, repo_path)
    if compile_checks:
        compile_argv = [item[0] for item in compile_checks]
        compile_cwds = [item[1] for item in compile_checks]
        return run_test_commands(
            repo_path,
            compile_argv,
            timeout=settings.test_timeout,
            logger=logger,
            source=source,
            plan_commands=plan_commands,
            command_cwds=compile_cwds,
            mode="scoped",
            scoped_from_failure=True,
            phase_labels=["compile check"] * len(compile_argv),
        )

    derived = derive_scoped_test_commands(
        prior_output,
        repo_path,
        base_commands=full_commands,
    )
    if not derived:
        return run_test_commands(
            repo_path,
            full_commands,
            timeout=settings.test_timeout,
            logger=logger,
            source=source,
            plan_commands=plan_commands,
            mode="full",
        )

    scoped_argv = [item[0] for item in derived]
    scoped_cwds = [item[1] for item in derived]
    scoped_result = run_test_commands(
        repo_path,
        scoped_argv,
        timeout=settings.test_timeout,
        logger=logger,
        source=source,
        plan_commands=plan_commands,
        command_cwds=scoped_cwds,
        mode="scoped",
        scoped_from_failure=True,
        phase_labels=["scoped"] * len(scoped_argv),
    )

    if not scoped_result.passed or not settings.scoped_test_before_review_full:
        return scoped_result

    full_result = run_test_commands(
        repo_path,
        full_commands,
        timeout=settings.test_timeout,
        logger=logger,
        source=source,
        plan_commands=plan_commands,
        mode="full",
        phase_labels=["full gate"] * len(full_commands),
    )
    return _merge_results(scoped_result, full_result)


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
