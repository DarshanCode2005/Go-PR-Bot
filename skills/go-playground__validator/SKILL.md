---
test_commands:
  - go test -race ./... -count=1
lint_commands:
  - go vet ./...
  - golangci-lint run
---

# go-playground/validator (v10)

**Module:** `github.com/go-playground/validator/v10`  
**Import:** `import "github.com/go-playground/validator/v10"`

Struct and field validation library driven by struct tags (`validate:"required,email"`). The repo is a **single root package** plus optional subpackages (`translations/`, `non-standard/validators/`). Run tests from the repository root.

## Testing

Upstream CI and Makefile use race + coverage:

```bash
go test -race ./... -count=1
```

Scoped runs (preferred when touching one area):

```bash
go test -race -run TestTagName ./...
go test -race -run TestTagName/subtest ./...
```

Benchmarks (only when changing hot paths in `cache.go`, `validator.go`, or `baked_in.go`):

```bash
go test -run=NONE -bench=. -benchmem ./...
```

Lint (`.golangci.yaml` present):

```bash
golangci-lint run
```

## Architecture (where to change what)

| Area | Files | Agent notes |
|------|-------|-------------|
| Baked-in tags (`required`, `email`, `uuid`, …) | `baked_in.go`, `regexes.go`, lookup tables | Register in `bakedInValidators`; document tag in `README.md`; add tests in `validator_test.go` |
| Tag parsing & caches | `cache.go` | Performance-sensitive; avoid allocations on success path |
| Execution engine | `validator.go`, `validator_instance.go` | `Struct`/`Var` entry points; uses reflection + pooled `validate` struct |
| Errors | `errors.go` | Callers use `errors.As(err, &validator.ValidationErrors)` |
| Options | `options.go` | e.g. `WithRequiredStructEnabled` (forward-compatible default before v11) |
| Struct/field custom validators | `struct_level.go`, `field_level.go` | User extensions via `RegisterStructValidation` / `RegisterValidation` |
| Translations | `translations/<locale>/` | Add locale strings when adding tags with i18n |
| Non-standard tags | `non-standard/validators/` | Opinionated validators (e.g. `NotBlank`) — keep niche tags here |
| Examples | `_examples/` | Standalone `main` packages (underscore excludes from module build) |

## Conventions

- Preserve **v10 API compatibility** — module path ends in `/v10`; no breaking exported API without explicit issue scope.
- **`Validate` is a singleton** — thread-safe; reuse one instance (see `validator_instance.go`).
- **`restrictedTags`** in `baked_in.go` cannot be overridden — do not alias or register those names.
- New baked-in tag checklist: implement validator → register in `bakedInValidators` → README table → `validator_test.go` (+ translations if user-facing messages).
- Error handling: return `ValidationErrors` or `InvalidValidationError` only; never use bare `error` comparisons (see README).
- Cross-field tags use `FieldLevel.GetStructFieldOK*` against the parent struct.
- Do not lower Go version in `go.mod`.

## Issue-driven fixes (typical)

- Tag semantics (`required`, `omitempty`, `dive`, `keys`, …): fix in `baked_in.go` and/or executor in `validator.go`; add regression test mirroring issue struct.
- Empty string / zero value edge cases: check `typeOmitEmpty` handling in cache/executor before changing tag logic.
- Custom types: use `RegisterCustomTypeFunc` (see `doc.go` and `_examples/custom/`).

## PR conventions

- PR template requires **tests for the change** (checkbox in `.github/PULL_REQUEST_TEMPLATE.md`).
- Keep diffs focused — validation library users depend on stable tag behavior.
- Mention benchmarks if touching cache/executor (`make bench` before/after).
- Commit: `fix: <tag/issue summary> (fixes #N)` or `feat:` for new tags.

## Avoid

- Unrelated refactors across `validator_test.go` (very large file).
- New exported types when `Register*` hooks suffice.
- Changing default validation behavior globally without tests and README note.
- Running only `-count=1` without `-race` (CI uses race detector).
