"""Shared utilities for go_agent."""

from __future__ import annotations


def normalize_file_path(path: str) -> str:
    """Normalize a repository-relative path for consistent lookups."""
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("/"):
        normalized = normalized[1:]
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
