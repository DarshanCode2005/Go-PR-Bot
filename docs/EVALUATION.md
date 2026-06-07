# Evaluation: Sample end-to-end run

This is a dry-run against [go-playground/validator#1348](https://github.com/go-playground/validator/issues/1348) (the `unix_addr` validation bug).

**Run ID:** `f3934252-659f-48b2-a936-765c7e7869dd`

```bash
go-agent run --repo go-playground/validator --issue 1348 --dry-run
```

## Summary

The pipeline ran all the way through: plan, code, integrate, five fix iterations, review, and a PR draft. Tests never went green before the fix-iteration cap, so the run ended with `status=failed` and `review.decision=reject` because max iterations were exceeded. That is normal for a dry-run when tests stay red.

The good news is that the agent plumbing worked: patches were generated, applied, and re-tested in a loop, and artifacts were written. The bad news is semantic. Issue #1348 changes how validation behaves, but the existing tests still expect an empty string to pass. The plan only touched one file, and in five fix attempts the agent never added the small guard that would have made `""` valid again.

## What happened

| Phase | Result |
|-------|--------|
| Fix iterations 1 to 5 | Each produced a patch, committed, and re-ran tests |
| Tests | Failed every time on the same test |
| Review | `decision=reject`, max iterations exceeded |
| Artifacts | `changes.patch`, `PR.md`, and `run.log` written |
| Status | `failed` (expected for dry-run when tests do not pass) |

All five fix patches applied cleanly, so the retry logic itself did its job.

## Why tests kept failing

Every iteration hit the same error:

```text
TestUnixAddrValidation - Index: 0 unix_addr failed ... ''
```

`TestUnixAddrValidation` expects:

| Input | Expected |
|-------|----------|
| `""` | pass (valid) |
| `"v.sock"` | pass (valid) |

The final patch in `changes.patch` added socket resolution via `os.Stat`:

```go
func isUnixAddrResolvable(fl FieldLevel) bool {
    addr := fl.Field().String()
    _, err := net.ResolveUnixAddr("unix", addr)
    // ...
    fileInfo, err := os.Stat(addr)  // os.Stat("") fails, returns false
```

An empty string used to pass because `ResolveUnixAddr` never failed. After adding `os.Stat`, `""` fails. The fix loop never added something like:

```go
if addr == "" {
    return true
}
```

The fix agent only edited `baked_in.go` because the plan listed one file. It never opened `validator_test.go`, so it kept tweaking `baked_in.go` without landing on the empty-string behavior the tests expect.

## Artifacts to review

Sample outputs from this run live in [`samples/f3934252-659f-48b2-a936-765c7e7869dd/`](../samples/f3934252-659f-48b2-a936-765c7e7869dd/). The layout matches a local `artifacts/{run_id}/` folder.

```bash
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/changes.patch
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/PR.md
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/run.log
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/plan.json
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/test_result.json
cat samples/f3934252-659f-48b2-a936-765c7e7869dd/review.json
```

The PR draft gets the problem right. The code is close but misses the `""` edge case.

## Options

### 1. Manual one-line fix (fastest way to green tests)

In the workspace clone, add this before `os.Stat`:

```go
if addr == "" {
    return true
}
```

Then verify:

```bash
cd workspaces/f3934252-659f-48b2-a936-765c7e7869dd/repo
go test -race -run TestUnixAddrValidation -count=1 .
```

### 2. Fresh run with a stronger model and more iterations

```bash
# .env example
GO_AGENT_MODEL_FAST=groq/llama-3.3-70b-versatile
GO_AGENT_MODEL_STRONG=groq/llama-3.3-70b-versatile
GO_AGENT_MAX_FIX_ITERATIONS=8

go-agent run --repo go-playground/validator --issue 1348 --dry-run
```

A new run might plan both `baked_in.go` and `validator_test.go`. Scoped test commands can also keep early fix iterations focused on `TestUnixAddrValidation`.

### 3. Use the artifacts as-is for the assignment

For a dry-run demo, the trace is already complete: plan, coder, integrator, five fix loops, PR draft. Calling out that `TestUnixAddrValidation` needs an empty-string guard is a fair evaluation write-up.

## Takeaways for reviewers

1. **Infrastructure:** The closed loop, integrator, and fixer retries behaved as designed.
2. **Semantic gaps:** When validation changes edge-case behavior, fixes need to account for what the tests expect, not just the implementation change.
3. **Plan scope:** A one-file plan is harder to finish when the issue spans both code and test expectations, especially with a small iteration budget.
4. **Model and iteration budget:** Smaller fast models on tricky OSS issues may need more fix iterations or a stronger tier.
