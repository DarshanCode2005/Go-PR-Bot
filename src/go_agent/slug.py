"""Slug helpers for branch names."""

from __future__ import annotations

import re

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_MULTI_DASH = re.compile(r"-+")


def slugify_issue_title(title: str, *, max_length: int = 40) -> str:
    """Convert an issue title into a safe branch name segment."""
    slug = title.strip().lower()
    slug = _NON_ALNUM.sub("-", slug)
    slug = _MULTI_DASH.sub("-", slug).strip("-")
    if not slug:
        slug = "issue"
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")
    return slug or "issue"


def issue_branch_name(issue_number: int, issue_title: str) -> str:
    slug = slugify_issue_title(issue_title)
    return f"agent/issue-{issue_number}-{slug}"
