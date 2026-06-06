---
test_commands:
  - go test ./... -count=1
lint_commands:
  - go vet ./...
  - golangci-lint run
---

# gin-gonic/gin

**Module:** `github.com/gin-gonic/gin`  
**Import:** `import "github.com/gin-gonic/gin"`  
**Default branch:** `master` (not `main`)

High-performance HTTP web framework: radix router (`tree.go`), middleware chain, request binding/validation, and response rendering. Core API lives in the root `gin` package; subpackages handle binding, rendering, JSON codecs, and small internals.

## Testing

CI runs **golangci-lint** first, then **`make test`** on Go 1.25+ with a matrix of build tags and `-race`. Agent default (fast, all packages):

```bash
go test ./... -count=1
```

CI-aligned runs (when touching concurrency, binding, or tag-specific code):

```bash
go test -race ./... -count=1
make test
```

Scoped runs (preferred when editing one area):

```bash
go test -run TestContext ./... -count=1
go test -run TestBinding ./binding/... -count=1
go test -run TestRender ./render/... -count=1
```

Build-tag variants exercised in CI (run when changing tagged files):

```bash
go test -tags nomsgpack ./... -count=1
go test -tags go_json ./... -count=1
go test --ldflags="-checklinkname=0" -tags sonic ./... -count=1
```

Benchmarks (router/hot paths — `tree.go`, `context.go`, `response_writer.go`):

```bash
go test -run=NONE -bench=. -benchmem ./...
```

Use **`CreateTestContext`** / **`CreateTestContextOnly`** from `test_helpers.go` for handler and middleware tests instead of spinning up real servers when possible.

Lint (`.golangci.yml`, golangci-lint v2):

```bash
go vet ./...
golangci-lint run
```

Format check (Makefile; CI-adjacent):

```bash
make fmt-check
```

## Architecture (where to change what)

| Area | Files / packages | Agent notes |
|------|------------------|-------------|
| Engine, server lifecycle | `gin.go` | `Engine`, `Default()`, `Run`/`RunTLS`/`RunListener`, trusted proxies, route registration |
| Request context | `context.go` | `Context` — bind, render, abort, params, client IP; largest surface for handler bugs |
| Radix router | `tree.go`, `path.go` | Performance-critical; zero-allocation routing; regression tests in `tree_test.go`, `routes_test.go` |
| Route groups | `routergroup.go` | `Group`, verb helpers (`GET`, `POST`, …), shared middleware |
| Response writer | `response_writer.go` | Status/body ordering, `Written()`, hijack; many “headers already sent” bugs |
| Built-in middleware | `logger.go`, `recovery.go`, `auth.go` | Default stack in `Default()`; avoid changing log format/behavior unless issue asks |
| Errors | `errors.go` | Typed `Error` with `ErrorTypeBind` / `ErrorTypeRender`; used by `c.Error()` |
| Modes / binding setup | `mode.go` | `GIN_MODE`, `SetMode`; wires default validator in `binding` |
| Request binding | `binding/` | JSON/form/query/URI/XML/YAML/TOML/msgpack; `default_validator.go` uses go-playground/validator |
| Response rendering | `render/` | JSON/HTML/redirect/stream; pairs with `Context.Render` / `JSON` / `XML` helpers |
| JSON codec selection | `codec/json/` | Build tags: default stdlib, `sonic`, `jsoniter`, `go_json` — see `docs/doc.md` Build Tags |
| Internal helpers | `internal/bytesconv`, `internal/fs` | Low-level; avoid expanding unless necessary |
| Singleton API | `ginS/` | Thin global wrapper around one `Engine` |
| Deprecated API | `deprecated.go` | Move removals here; keep tests in `deprecated_test.go` |
| Static / templates | `fs.go`, `LoadHTML*` in `gin.go` | File serving and HTML template loading |
| Version | `version.go` | Release version string |
| User docs (features) | `docs/doc.md` | **New features documented here**, not README (per CONTRIBUTING) |
| Examples | `examples/` | README only — not a Go module subpackage |

## Build tags (do not break CI matrix)

| Tag | Effect |
|-----|--------|
| `nomsgpack` | Uses `binding_nomsgpack.go`; disables msgpack in binding/render |
| `sonic` | Sonic JSON in `codec/json/` (linux/windows/darwin) |
| `go_json` | goccy/go-json codec |
| `jsoniter` | json-iterator codec |
| `appengine` | `context_appengine.go` build variant |

When editing `binding/binding.go`, `binding_nomsgpack.go`, or msgpack files, run both default and `-tags nomsgpack` tests.

## Conventions

- **API stability** — gin is widely used; avoid breaking exported API without explicit issue/maintainer scope.
- **Minimal diffs** — match existing style; no drive-by refactors in `context.go` or `tree.go`.
- **Tests required** — add or extend `*_test.go` beside changed code; mirror issue repro as a table-driven test when possible.
- **New features** — document in `docs/doc.md`; keep README release/marketing content separate.
- **Middleware** — follow `HandlerFunc` chain pattern; call `c.Next()` unless aborting.
- **Binding/validation** — prefer fixes in `binding/` for decode/validate issues; `Context.ShouldBind*` delegates there.
- **Do not lower Go version** in `go.mod` (currently Go 1.25+).
- **PR target branch:** `master`.
- **PR size:** squash to **≤ 2 commits** before opening (CONTRIBUTING).

## Issue-driven fixes (typical)

| Symptom | Likely location |
|---------|-----------------|
| Route not matched / param parsing | `tree.go`, `path.go`, `routergroup.go` |
| Wrong status or double response write | `response_writer.go`, `context.go` render/abort paths |
| Bind/validate failure | `binding/`, `binding/default_validator.go`, `context.go` bind helpers |
| Panic not recovered | `recovery.go`, middleware order in tests |
| Client IP / proxy headers | `Engine.SetTrustedProxies`, `Context.ClientIP` in `gin.go`/`context.go` |
| JSON encode/decode perf or behavior | `codec/json/`, `render/json.go`, build tags |
| Logger format / skip paths | `logger.go`, `routergroup.go` route logging |
| CORS/auth examples | Usually out of scope — direct users to `gin-contrib` unless core hook needed |

## PR conventions

- Open against **`master`**; follow `.github/PULL_REQUEST_TEMPLATE.md` checklist.
- **Tests must pass** in GitHub Actions (`lint` job + `make test` matrix).
- Commit messages: `fix: <summary> (fixes #N)` or `feat:` for new API; `docs:` for doc-only.
- Reference the GitHub issue number in PR body.
- If changing router or binding behavior, note any benchmark impact.

## Avoid

- Large router rewrites for small bugfixes.
- Changing default `Logger()` or `Recovery()` output without explicit issue request.
- Documenting new features only in README (use `docs/doc.md`).
- Editing `benchmarks_test.go` / `githubapi_test.go` unless benchmark-related.
- Running only root package tests when `binding/` or `render/` changed.
- Force-pushing or multi-commit PRs (> 2 commits).
