---
name: test-awareness
description: Ensure plans account for Go test expectations when behavior changes
---

## When to use

Behavior, validation, or API semantics changes.

## Rules

1. Read failing or related `*_test.go` before editing production code
2. Prefer matching production code to existing tests unless issue says otherwise
3. Include test file paths in the plan when tests encode expected behavior
4. List test names in `acceptance_criteria`
