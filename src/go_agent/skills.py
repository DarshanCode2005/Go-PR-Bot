"""Repo skill loading and test/lint command resolution."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from go_agent.workspace import repo_slug

if TYPE_CHECKING:
    from go_agent.planner import FixPlan

_SKILLS_ROOT = Path(__file__).resolve().parents[2] / "skills"
_BASH_BLOCK = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_GO_TEST_LINE = re.compile(r"^\s*(go test\s+.+)$", re.MULTILINE)


def _split_inline_bracket_list(inline: str) -> list[str]:
    """Split a YAML-style `[a, b]` list, respecting quoted strings."""
    inner = inline[1:-1]
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for ch in inner:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch == ",":
            item = "".join(current).strip().strip("\"'")
            if item:
                items.append(item)
            current = []
        else:
            current.append(ch)
    item = "".join(current).strip().strip("\"'")
    if item:
        items.append(item)
    return items


def load_skill_text(repo: str) -> str:
    """Load repo skill markdown, falling back to skills/_default/SKILL.md."""
    repo_skill = _SKILLS_ROOT / repo_slug(repo) / "SKILL.md"
    default_skill = _SKILLS_ROOT / "_default" / "SKILL.md"
    path = repo_skill if repo_skill.is_file() else default_skill
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _parse_frontmatter_list_commands(skill_text: str, key: str) -> list[str] | None:
    if not skill_text.startswith("---"):
        return None
    parts = skill_text.split("---", 2)
    if len(parts) < 3:
        return None

    prefix = f"{key}:"
    commands: list[str] = []
    in_block = False
    for line in parts[1].splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            in_block = True
            inline = stripped.split(":", 1)[1].strip()
            if inline and inline.startswith("["):
                commands.extend(_split_inline_bracket_list(inline))
            elif inline:
                commands.append(inline.strip("\"'"))
            continue
        if in_block:
            if stripped.startswith("- "):
                commands.append(stripped[2:].strip().strip("\"'"))
                continue
            if stripped and not stripped.startswith("#"):
                break
    cleaned = [item for item in commands if item]
    return cleaned or None


def _parse_frontmatter_test_commands(skill_text: str) -> list[str] | None:
    return _parse_frontmatter_list_commands(skill_text, "test_commands")


def _parse_bash_block_test_commands(skill_text: str) -> list[str] | None:
    for match in _BASH_BLOCK.finditer(skill_text):
        block = match.group(1)
        for go_match in _GO_TEST_LINE.finditer(block):
            return [go_match.group(1).strip()]
    for go_match in _GO_TEST_LINE.finditer(skill_text):
        return [go_match.group(1).strip()]
    return None


def parse_skill_test_commands(skill_text: str) -> list[str] | None:
    """Parse explicit test command overrides from skill markdown."""
    if not skill_text.strip():
        return None
    return _parse_frontmatter_test_commands(skill_text) or _parse_bash_block_test_commands(
        skill_text
    )


def resolve_test_commands(plan: FixPlan, repo: str) -> tuple[list[str], str]:
    """Resolve commands from repo skill override or plan.test_commands."""
    repo_skill = _SKILLS_ROOT / repo_slug(repo) / "SKILL.md"
    if repo_skill.is_file():
        overrides = parse_skill_test_commands(repo_skill.read_text(encoding="utf-8"))
        if overrides:
            return overrides, "skill_override"
    return list(plan.test_commands), "plan"


def _parse_frontmatter_lint_commands(skill_text: str) -> list[str] | None:
    return _parse_frontmatter_list_commands(skill_text, "lint_commands")


def parse_skill_lint_commands(skill_text: str) -> list[str] | None:
    """Parse explicit lint command overrides from skill markdown frontmatter."""
    if not skill_text.strip():
        return None
    return _parse_frontmatter_lint_commands(skill_text)


def default_lint_commands(repo_path: Path) -> list[str]:
    """Default vet + optional golangci-lint when config and binary are present."""
    commands = ["go vet ./..."]
    _golangci_configs = (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json")
    if shutil.which("golangci-lint") and any(
        (repo_path / name).is_file() for name in _golangci_configs
    ):
        commands.append("golangci-lint run")
    return commands


def resolve_lint_commands(
    repo: str,
    repo_path: Path,
) -> tuple[list[str], Literal["default", "skill_override"]]:
    """Resolve lint commands from repo skill override or defaults."""
    repo_skill = _SKILLS_ROOT / repo_slug(repo) / "SKILL.md"
    if repo_skill.is_file():
        overrides = parse_skill_lint_commands(repo_skill.read_text(encoding="utf-8"))
        if overrides:
            return overrides, "skill_override"
    return default_lint_commands(repo_path), "default"
