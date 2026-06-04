# Default Go OSS skill

## Testing
- Prefer scoped tests: `go test ./path/to/pkg -count=1`
- Fall back to `go test ./...` only when plan affects multiple packages
- Always run with `-count=1` to avoid cache masking failures

## Lint / format
- `go vet ./...`
- `gofmt -l` must be empty on changed files
- Use `golangci-lint run` only if `.golangci.yml` exists in repo root

## Code change rules
- Match existing naming and error wrapping (`fmt.Errorf`, `%w`)
- Add or update `_test.go` for behavior changes
- Do not bump module major version or change public API without issue asking for it
- No unrelated refactors

## PR conventions
- Commit: `fix: <short> (fixes #N)` or `feat:` when appropriate
- PR body must include **Testing** with exact commands run
