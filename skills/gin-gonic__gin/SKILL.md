---
test_commands:
  - go test ./... -count=1
---

# gin-gonic/gin

Load upstream README for latest test instructions. Typical validation:

```bash
go test ./... -count=1
```

## Conventions
- Middleware and handlers follow existing patterns in `gin.go` and `context.go`
- Prefer minimal API surface changes; gin is a stable HTTP framework
- Benchmarks live under `*_test.go` — do not break benchmark-only packages without cause

## Avoid
- Large router refactors for small bugfixes
- Changing default Logger behavior unless issue explicitly requests it
