---
test_commands:
  - GL_TEST_RUN=1 go test ./... -count=1 -parallel 2
lint_commands:
  - go vet ./...
  - golangci-lint run
---

# golangci/golangci-lint

**Module:** `github.com/golangci/golangci-lint/v2`  
**Import:** `import "github.com/golangci/golangci-lint/v2/pkg/..."`  
**Binary:** `cmd/golangci-lint`  
**Default branch:** `main`

Fast, parallel linters runner built on **cobra** (CLI), **go/analysis** (most linters), config v2 YAML, result processors, and formatters. The repo is large (~180 packages); most issue fixes touch one linter under `pkg/golinters/` or core pipeline code in `pkg/lint/`, `pkg/config/`, or `pkg/commands/`.

## Testing

CI (`make test` on Go 1.25–1.26, Unix/macOS/Windows) does three things:

1. `go build -o golangci-lint ./cmd/golangci-lint`
2. `GL_TEST_RUN=1 ./golangci-lint run -v` (dogfood on repo `.golangci.yml`)
3. `GL_TEST_RUN=1 go test -v -parallel 2 ./...`

Agent default (Makefile `go test` step; set **`GL_TEST_RUN=1`** — required by integration tests):

```bash
GL_TEST_RUN=1 go test ./... -count=1 -parallel 2
```

Full CI parity (needs **CGO_ENABLED=1**, build, and time):

```bash
make test
```

Scoped runs (strongly preferred when changing one linter or package):

```bash
GL_TEST_RUN=1 go test ./pkg/golinters/errcheck/... -count=1
GL_TEST_RUN=1 go test ./pkg/config/... -count=1
GL_TEST_RUN=1 go test ./pkg/lint/... -count=1
GL_TEST_RUN=1 go test ./pkg/commands/... -count=1
```

Integration tests (end-to-end CLI against `test/testdata/`):

```bash
make build
GL_TEST_RUN=1 go test ./test -count=1 -run TestSourcesFromTestdata/output.go
GL_TEST_RUN=1 go test ./test -count=1 -run TestFix/multiple-issues-fix.go
```

Lint (repo has `.golangci.yml`; CI also runs `golangci-lint-action`):

```bash
go vet ./...
golangci-lint run
# after build, dogfood like CI:
./golangci-lint run -v
```

**Do not edit `.golangci.yml`** when adding a linter (project policy — see new-linter checklist).

## Architecture (where to change what)

| Area | Path | Agent notes |
|------|------|-------------|
| CLI entry | `cmd/golangci-lint` | Thin `main`; delegates to `pkg/commands` |
| Cobra commands | `pkg/commands/` | `run.go`, `root.go`, `linters.go`, `fmt.go`, `migrate.go`, `help*.go` |
| Config loading | `pkg/config/` | YAML v2 schema, loader, linters/formatters settings |
| Lint orchestration | `pkg/lint/` | `runner.go`, `lintersdb/` — enabled linters, processor chain |
| Linter registry | `pkg/lint/lintersdb/` | `builder_linter.go` — register linters, `WithSince`, `WithLoadMode`, sort order |
| Linter wrappers | `pkg/golinters/<name>/` | One dir per linter: wrapper + `testdata/` + `*_integration_test.go` |
| go/analysis glue | `pkg/goanalysis/` | `NewLinterFromAnalyzer`, load modes |
| Result pipeline | `pkg/result/processors/` | nolint filter, severity, path relativity, uniq-by-line, fixer |
| Output | `pkg/printers/` | text, JSON, SARIF, etc. |
| Formatters | `pkg/goformatters/` | gci, gofmt, gofumpt, goimports, golines, … |
| Integration tests | `test/` | `run_test.go`, `fix_test.go`, `linters_test.go`; uses `test/testshared/` |
| Reference config | `.golangci.next.reference.yml` | **Edit for new linters** (not `.golangci.reference.yml`) |
| JSON schema | `jsonschema/golangci.next.jsonschema.json` | Update for new config fields (not `golangci.jsonschema.json`) |
| GitHub Action asset | `assets/github-action-config.json` | Generated; `make fast_generate` |
| Docs site | `docs/`, `scripts/website/` | Hugo; not run by default `go test` |

## Adding or fixing a linter

Follow [`.github/new-linter-checklist.md`](https://github.com/golangci/golangci-lint/blob/main/.github/new-linter-checklist.md):

1. **New linters:** open a [Discussion → New Linter Proposals](https://github.com/golangci/golangci-lint/discussions/new?category=new-linter-proposals) **before** a PR — unapproved PRs are closed.
2. **Do not bump linter deps** via PR — Dependabot handles weekly updates; only original linter authors may force major bumps.
3. Wrapper in `pkg/golinters/<lintername>/<lintername>.go` using `go/analysis`.
4. Register in `pkg/lint/lintersdb/builder_linter.go` (alphabetical, `WithSince` = next minor `v1.X.0`).
5. Integration tests: default config + config-with-settings; `testdata/` with stdlib import.
6. Update `.golangci.next.reference.yml` only — **never** `.golangci.reference.yml` or repo `.golangci.yml`.
7. Load mode: `LoadModeSyntax` → no `WithLoadForGoAnalysis()`; `LoadModeTypesInfo` → requires it.

Fixing linter behavior: change wrapper + `testdata/` + integration test; run scoped `go test ./pkg/golinters/<name>/...`.

## Conventions

- **Go version in `go.mod`** — maintainers only; currently Go 1.25+ (`latest-1` policy).
- **Module path `/v2`** — v2 API; import paths include `/v2`.
- **CLA required** on PRs.
- **PRs from org forks are rejected** — use personal forks only.
- **Config v2** — `version: "2"` in YAML; migration via `golangci-lint migrate`.
- **Processors order matters** — changes in `pkg/lint/runner.go` affect all linters.
- **`go mod tidy`** — CI fails if `go.mod`/`go.sum` drift; run after dependency changes.

## Issue-driven fixes (typical)

| Symptom | Likely location |
|---------|-----------------|
| Single linter false positive/negative | `pkg/golinters/<name>/`, its `testdata/` |
| Config not applied / parse error | `pkg/config/`, `jsonschema/` |
| nolint / exclude rules wrong | `pkg/result/processors/` (nolint, exclusion paths) |
| CLI flag or run behavior | `pkg/commands/run.go`, `flagsets.go` |
| Output format / SARIF / JSON | `pkg/printers/` |
| Performance / cache | `internal/cache/`, `pkg/goanalysis/load/` |
| Formatter (--fix) | `pkg/goformatters/`, `test/fix_test.go` |
| Windows-specific test skip | `test/run_test.go`, `test/testshared/` |

## PR conventions

- Target **`main`**; sign **CLA** when prompted.
- **No linter version bump PRs** (Dependabot except author-initiated major).
- **No new linter without approved discussion.**
- New linter PRs: address review as **commits** (no squash during review per checklist).
- Commit: `fix: <area> (fixes #N)` or `feat(linters): add <name>`.
- Run `make test` or at minimum scoped tests + `go vet` before push.

## Avoid

- Editing `.golangci.yml` or `.golangci.reference.yml` for linter additions.
- PRs from GitHub org forks.
- Unrelated refactors across `pkg/golinters/` (900+ files).
- Running full `./...` tests without `GL_TEST_RUN=1`.
- Changing `go.mod` Go version without maintainer scope.
- Modifying generated files without `make fast_generate` / website scripts where applicable.
