---
test_commands:
  - go test ./... -count=1
lint_commands:
  - go vet ./...
  - golangci-lint run
---

# spf13/cobra

**Module:** `github.com/spf13/cobra`  
**Import:** `import "github.com/spf13/cobra"`  
**Default branch:** `main`  
**License:** Apache 2.0 — preserve copyright header on new/changed files

CLI framework for nested commands, POSIX flags (via **pflag**), help generation, shell completions, and doc export. Root package is `cobra`; documentation generators live in **`doc/`**. User-facing docs also live on [cobra.dev](https://cobra.dev) and in `site/content/` (Hugo site, not a Go package).

## Testing

Contributing and Makefile use:

```bash
go test ./... -count=1
make test
```

CI runs **`make richtest`** (same tests, richgo wrapper) on Go 1.17–1.24 across Unix and Windows (MINGW64). Agent default matches Makefile:

```bash
go test ./... -count=1
```

Scoped runs (preferred when editing one area):

```bash
go test -run TestCommand ./... -count=1
go test -run TestCompletion ./... -count=1
go test -run TestBash ./... -count=1
go test ./doc/... -count=1
```

Format check (required before PR — `make all` = fmt + test):

```bash
gofmt -s -w .
# CI/Makefile check (no write):
test -z "$(gofmt -l .)"
```

Lint (`.golangci.yml`, golangci-lint v2; CI runs before tests):

```bash
go vet ./...
golangci-lint run
```

CI also runs **Apache license header** check via `addlicense` — new `.go` files need the standard Cobra Authors header block.

## Architecture (where to change what)

| Area | Files / packages | Agent notes |
|------|------------------|-------------|
| Command model & execution | `command.go` | `Command` struct, `Execute`/`ExecuteC`, subcommand routing, flag sets, help/version/completion init — largest file; surgical edits only |
| Globals & templates | `cobra.go` | `EnablePrefixMatching`, `EnableCommandSorting`, help templates, `OnInitialize`/`OnFinalize`; legacy `Gt`/`Eq` kept for compat |
| Positional args | `args.go` | `MinimumNArgs`, `MaximumNArgs`, `ExactArgs`, `OnlyValidArgs`, etc. |
| Shell completion core | `completions.go` | Hidden `__complete` / `__completeNoDesc`, directives, flag/arg completion funcs |
| Bash completions | `bash_completions.go`, `bash_completionsV2.go` | Legacy vs V2 generators; match issue shell/version |
| Other shell scripts | `zsh_completions.go`, `fish_completions.go`, `powershell_completions.go` | Shell-specific output; paired `*_test.go` |
| Completion helpers | `shell_completions.go` | `MarkFlagRequired`, `MarkFlagFilename`, completion annotations |
| Flag groups | `flag_groups.go` | `MarkFlagsRequiredTogether`, `MarkFlagsOneRequired`, `MarkFlagsMutuallyExclusive` |
| Active help (completions) | `active_help.go` | `AppendActiveHelp`, `COBRA_ACTIVE_HELP` env |
| Windows mousetrap | `command_win.go`, `command_notwin.go` | Explorer-launched CLI guard; uses dual build tags (`//go:build` + `// +build`) for Go 1.15 compat |
| Doc generation | `doc/` | `GenMarkdown`, `GenMan`, `GenYaml`, `GenReST`; tests in `doc/*_test.go` |
| Site docs (markdown) | `site/content/` | User guides — not executed by `go test`; update when behavior docs change |
| Flags dependency | `github.com/spf13/pflag` | Persistent/local flags merged in `command.go`; flag bugs may need pflag behavior awareness |

## Command execution flow

Understanding `ExecuteC()` helps route bugs:

1. `InitDefaultHelpCmd`, `InitDefaultCompletionCmd`, help/version flags
2. Parse flags via **pflag** (`FParseErrWhitelist` for unknown flags)
3. Validate positional args (`Args` / `PositionalArgs`)
4. Run hooks: `PersistentPreRun` → `PreRun` → `Run` → `PostRun` → `PersistentPostRun` (traversal controlled by `EnableTraverseRunHooks`)
5. Suggestions on unknown subcommands via Levenshtein (`findSuggestions` in `command.go`)

## Conventions

- **Backward compatibility** — widely imported (Kubernetes, Hugo, gh CLI); avoid breaking exported API.
- **Go version** — `go.mod` targets Go 1.15; keep dual build-tag syntax where present until maintainers bump minimum Go.
- **Tests required** — add/extend `*_test.go` beside changed code; table-driven tests are common in `command_test.go`.
- **Formatting** — `gofmt -s`; CI fails on unformatted code (`make fmt`).
- **pflag** — local vs persistent flags; `MergeFlags` behavior affects completion and help output.
- **Hidden commands** — completion uses `__complete`; do not rename without updating all shell generators.
- **PRs require CLA** — contributors are prompted to sign on first PR (see CONTRIBUTING.md).

## Issue-driven fixes (typical)

| Symptom | Likely location |
|---------|-----------------|
| Wrong/missing shell completions | `completions.go` + shell file (`bash_completionsV2.go`, `zsh_completions.go`, …) |
| Flag not recognized / parse error | `command.go` flag merge/parse paths; check pflag annotations |
| Required/mutually exclusive flags | `flag_groups.go`, `shell_completions.go` |
| Positional arg validation | `args.go`, `Command.Args` wiring in `command.go` |
| Help/usage template wrong | `command.go` templates; `cobra.go` `AddTemplateFunc` |
| Unknown command suggestions | `findSuggestions`, `EnableCaseInsensitive`, `Aliases` |
| Windows-only launch behavior | `command_win.go`, `MousetrapHelpText` |
| Man/markdown/yaml doc output | `doc/` package |
| Active help in completions | `active_help.go` |

## PR conventions

- Target branch: **`main`**
- Run **`make all`** (fmt + test) or at minimum `go test ./...` + `golangci-lint run`
- Sign **CLA** when prompted
- Add regression test mirroring the issue repro
- Commit: `fix: <summary> (fixes #N)` or `feat:` for new behavior
- New `.go` files: Apache 2.0 header (`// Copyright 2013-2023 The Cobra Authors`)

## Avoid

- Drive-by refactors in `command.go` (2000+ lines)
- Changing global defaults (`EnablePrefixMatching`, sorting, traverse hooks) without explicit issue scope
- Removing deprecated template helpers (`Gt`, `Eq`) — marked for v2 only
- Shell fixes in one generator without checking others (bash v1 vs v2, zsh, fish, powershell)
- Editing `site/content/` only without tests when the fix is in library code
- Lowering test coverage for completion edge cases — completion tests are extensive for a reason
