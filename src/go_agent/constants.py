"""Shared constants for the go-agent CLI and pipeline."""

APPROVED_REPOS = (
    "gin-gonic/gin",
    "spf13/cobra",
    "go-playground/validator",
    "golangci/golangci-lint",
)

APPROVED_REPOS_HELP = ", ".join(APPROVED_REPOS)
