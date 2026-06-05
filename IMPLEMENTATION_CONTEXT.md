# Implementation Context

Step-by-step record of what was built for each backlog item in [Go-PR-Bot](https://github.com/DarshanCode2005/Go-PR-Bot). Use this to understand **why** each module exists, **how** it fits into the pipeline, and **what** artifacts/tests were added.

**How to maintain:** When a new GitHub issue is implemented, append a new section under [Implemented issues](#implemented-issues) using the same template. Do not rewrite earlier sections unless correcting factual errors.

**Issue numbering:** Backlog numbers in `docs/GITHUB_ISSUES.md` are off-by-one from GitHub issue numbers. See `docs/GITHUB_ISSUE_MAP.md` for the mapping.

| Backlog | GitHub | Status |
|---------|--------|--------|
| #1 Initialize package | [#2](https://github.com/DarshanCode2005/Go-PR-Bot/issues/2) | Done |
| #2 Typer CLI skeleton | [#3](https://github.com/DarshanCode2005/Go-PR-Bot/issues/3) | Done |
| #3 Config and logging | [#4](https://github.com/DarshanCode2005/Go-PR-Bot/issues/4) | Done |
| #4 Clone workspace | [#5](https://github.com/DarshanCode2005/Go-PR-Bot/issues/5) | Done |
| #5 Issue branch | [#6](https://github.com/DarshanCode2005/Go-PR-Bot/issues/6) | Done |
| #6 Patches helper | [#7](https://github.com/DarshanCode2005/Go-PR-Bot/issues/7) | Done |
| #7 Fetch issue metadata | [#8](https://github.com/DarshanCode2005/Go-PR-Bot/issues/8) | Done |
| #8 Scope hints | [#9](https://github.com/DarshanCode2005/Go-PR-Bot/issues/9) | Done |
| #9 PR generator | [#10](https://github.com/DarshanCode2005/Go-PR-Bot/issues/10) | Not started |
| … | … | … |

---

## Current pipeline state

`go-agent run` today performs setup through scope extraction, then exits with code 1 because the LangGraph agent loop is not wired yet.

```
go-agent run --repo <owner/name> --issue <N>
  │
  ├─ create_run_context()          → UUID run_id, artifact + workspace dirs
  ├─ configure_run_logging()       → console + artifacts/{run_id}/run.log
  ├─ ensure_repo_cloned()          → workspaces/{run_id}/repo
  ├─ fetch_issue_context()         → IssueContext via gh or PyGithub
  ├─ ensure_issue_open_or_forced() → blocks closed issues unless --force
  ├─ write_issue_context()         → issue_context.json
  ├─ prepare_scope()               → heuristic (+ optional LLM) scope hints
  ├─ write_scope_hints()           → scope_hints.json
  ├─ create_issue_branch()         → agent/issue-{N}-{slug}
  ├─ write_branch_meta()           → branch_meta.json
  ├─ [optional] apply_patch_and_commit()  → --patch-file dev path
  └─ exit 1 — "Pipeline not implemented yet"
```

**Approved repos:** `gin-gonic/gin`, `spf13/cobra`, `go-playground/validator`, `golangci/golangci-lint`

**Test suite:** 55 tests, `pytest -q && ruff check src tests`

---

## Artifacts per run

All artifacts live under `artifacts/{run_id}/`:

| File | Introduced by | Contents |
|------|---------------|----------|
| `run.log` | Backlog #3 | Structured run log with `run_id` in every line |
| `repo_meta.json` | Backlog #4 | repo, remote HEAD SHA, cache hit, paths |
| `issue_context.json` | Backlog #7 | Full `IssueContext` (title, body, labels, state, comments) |
| `scope_hints.json` | Backlog #8 | `ScopeBundle`: scope_hints, issue_number, repo |
| `branch_meta.json` | Backlog #5 | branch name, base SHA, default branch, issue info |
| `changes.patch` | Backlog #6 | `git diff` from base SHA (when `--patch-file` used) |

Workspace clone: `workspaces/{run_id}/repo`

Shared cache: `workspaces/_cache/{owner__repo}/` (shallow clone + `meta.json`)

---

## Source file map

| Module | Role |
|--------|------|
| `cli.py` | Typer entry; orchestrates setup steps |
| `constants.py` | Approved repo allowlist |
| `config.py` | Pydantic settings from env / `.env` |
| `run_context.py` | Per-run UUID, artifact and workspace paths |
| `logging_config.py` | Console + file logging with run_id adapter |
| `workspace.py` | Shallow clone with shared cache |
| `git_util.py` | Shared `run_git()` subprocess helper |
| `slug.py` | Issue title → branch slug |
| `branching.py` | Create/reuse `agent/issue-N-slug` branch |
| `patches.py` | Apply unified diff, commit, export patch |
| `github_issues.py` | Fetch and model issue metadata |
| `issue_scope.py` | Heuristic + optional LLM scope hint extraction |
| `context_builder.py` | Stub: `prepare_scope`, `write_scope_hints` |

---

## Implemented issues

---

### Backlog #1 — Initialize Python package and tooling

**GitHub:** [#2](https://github.com/DarshanCode2005/Go-PR-Bot/issues/2)  
**Commit:** `3ea6d20` Initial Commit  
**PR:** (bootstrap)

#### What was built

- `pyproject.toml` — package `go-agent`, Python ≥3.11, hatchling build
- Dependencies: typer, pydantic, pydantic-settings, litellm, langgraph, pygithub, gitpython, httpx, rich
- Dev extras: pytest, ruff, pytest-asyncio
- Optional extras: `mcp`, `rag` (chromadb), `memory` (mem0ai)
- `src/go_agent/__init__.py` with version
- `.gitignore`, `.env.example`
- `tests/` scaffold with `conftest.py`

#### Key decisions

- `src/` layout with hatch wheel packaging
- Ruff line-length 100, target Python 3.11
- Entry point: `go-agent = go_agent.cli:app`

#### Verification

```bash
pip install -e ".[dev]"
pytest
ruff check src tests
```

---

### Backlog #2 — Typer CLI skeleton

**GitHub:** [#3](https://github.com/DarshanCode2005/Go-PR-Bot/issues/3)  
**Commit:** `c9c40cb` feat(cli): complete Typer skeleton with help and validation  
**PR:** [#46](https://github.com/DarshanCode2005/Go-PR-Bot/pull/46)

#### What was built

- `src/go_agent/cli.py` — Typer app with `run` and `version` commands
- `src/go_agent/constants.py` — `APPROVED_REPOS` tuple and help string
- Repo validation: regex `^[\w.-]+/[\w.-]+$` + allowlist check
- Flags on `run`:
  - `--repo` (required)
  - `--issue` (required)
  - `--dry-run / --no-dry-run` (default dry-run true)
  - `--create-pr` (requires `--no-dry-run`)
- `--create-pr` + `--dry-run` → exit code 2 with clear error
- Epilog lists approved repos on help

#### Tests

- `tests/test_cli.py` — help text, missing args, invalid repo, flag validation

#### Notes

- Pipeline stub exits 1 after setup; full graph deferred to LangGraph issues

---

### Backlog #3 — Configuration and logging

**GitHub:** [#4](https://github.com/DarshanCode2005/Go-PR-Bot/issues/4)  
**Commit:** `b91ae28` feat(config): add settings, run context, and structured logging (fixes #4)  
**PR:** (merged to main)

#### What was built

**`config.py` — `Settings` (pydantic-settings)**

| Setting | Env var | Default |
|---------|---------|---------|
| `work_dir` | `GO_AGENT_WORK_DIR` | `./workspaces` |
| `artifacts_dir` | `GO_AGENT_ARTIFACTS_DIR` | `./artifacts` |
| `log_level` | `GO_AGENT_LOG_LEVEL` | `INFO` |
| `max_fix_iterations` | `GO_AGENT_MAX_FIX_ITERATIONS` | `5` |
| `max_issue_comments` | `GO_AGENT_MAX_ISSUE_COMMENTS` | `20` |
| `openai_api_key` | `OPENAI_API_KEY` | None |
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | None |
| `github_token` | `GITHUB_TOKEN` | None |
| `model_fast` | `GO_AGENT_MODEL_FAST` | `gpt-4o-mini` |
| `model_strong` | `GO_AGENT_MODEL_STRONG` | `gpt-4o` |

- `get_settings()` cached via `@lru_cache`
- `clear_settings_cache()` for tests
- Log level validated to DEBUG/INFO/WARNING/ERROR

**`run_context.py` — `RunContext`**

- Each run gets UUID `run_id`
- Creates `artifacts/{run_id}/` and `workspaces/{run_id}/`
- Exposes `artifact_dir`, `log_path`, `workspace_dir`

**`logging_config.py`**

- Format: `%(asctime)s %(levelname)s [run_id=...] %(name)s: %(message)s`
- Handlers: stderr console + `artifacts/{run_id}/run.log`
- Returns `_RunIdAdapter` logger scoped to run

#### Tests

- `tests/test_config.py` — settings loading, env overrides, validation
- `tests/test_run_context.py` — UUID uniqueness, directory creation

#### CLI integration

- `run` calls `get_settings()` → `create_run_context()` → `configure_run_logging()` at start

---

### Backlog #4 — Clone approved repo into workspace

**GitHub:** [#5](https://github.com/DarshanCode2005/Go-PR-Bot/issues/5)  
**Commit:** `66f606d` feat(workspace): shallow clone approved repos with cache  
**PR:** [#47](https://github.com/DarshanCode2005/Go-PR-Bot/pull/47)

#### What was built

**`workspace.py`**

- `assert_repo_allowed()` — raises `RepoNotAllowedError` if not in allowlist
- `ensure_repo_cloned(repo, ctx, logger)`:
  1. Resolve remote HEAD via `git ls-remote --symref`
  2. Check shared cache at `workspaces/_cache/{owner__repo}/`
  3. Cache valid when `meta.json` remote_head matches and local HEAD matches
  4. On miss: shallow clone to temp dir, atomic rename into cache
  5. Local clone from cache into `workspaces/{run_id}/repo`
  6. Write `repo_meta.json` artifact

**`git_util.py`**

- `run_git(args, cwd)` — subprocess wrapper, 300s timeout, `GitCommandError` on failure

#### Key decisions

- Shallow clone (`--depth 1`) for speed
- Shared cache avoids re-downloading on every run
- Atomic cache update via temp dir + `os.replace` (handles concurrent races)
- Re-run within same workspace skips clone if `.git` already exists

#### Tests

- `tests/test_workspace.py` — allowlist, cache hit/miss, meta artifact, bare repo fixture

#### Errors

- `RepoNotAllowedError` → CLI exit 2
- `CloneError` → CLI exit 1

---

### Backlog #5 — Create issue branch

**GitHub:** [#6](https://github.com/DarshanCode2005/Go-PR-Bot/issues/6)  
**Commit:** `e2940bf` feat(branch): create agent/issue-N-slug branch and record base SHA (fixes #6)  
**PR:** [#48](https://github.com/DarshanCode2005/Go-PR-Bot/pull/48)

#### What was built

**`slug.py`**

- `slugify_issue_title(title, max_length=40)` — lowercase, non-alnum → dash, trim
- `issue_branch_name(n, title)` → `agent/issue-{n}-{slug}`

**`branching.py`**

- `BranchInfo` dataclass: branch_name, base_sha, default_branch, issue_number, issue_title
- `resolve_default_branch()` — origin/HEAD, fallback main/master, scan remote branches
- `create_issue_branch()`:
  - Checkout default branch
  - Record base SHA at HEAD
  - Create branch or checkout existing if already present
  - On existing branch: `merge-base` with default branch for true base SHA
- `write_branch_meta()` → `branch_meta.json`

#### Tests

- `tests/test_slug.py` — slugify edge cases, branch name format
- `tests/test_branching.py` — branch creation, reuse, default branch resolution
- `tests/test_cli_branch.py` — CLI integration with branch step

#### CLI integration

- Runs after issue fetch; uses `issue_ctx.title` for slug

---

### Backlog #6 — Apply patches and commit helper

**GitHub:** [#7](https://github.com/DarshanCode2005/Go-PR-Bot/issues/7)  
**Commit:** `77705f8` feat(patches): apply unified diff, commit, and export changes.patch  
**PR:** [#49](https://github.com/DarshanCode2005/Go-PR-Bot/pull/49)

#### What was built

**`patches.py`**

- `format_commit_message(summary, issue_number)` → `fix: {summary} (fixes #{N})`
- `apply_unified_patch()` — write temp file, `git apply --check` then apply
- `commit_all()` — `git add -A`, `git commit`, return SHA
- `export_changes_patch()` — `git diff {base_sha}` to file
- `apply_patch_and_commit()` — full flow:
  1. If branch already has commits beyond base: re-export patch, return existing commit
  2. Else: apply → stage → export patch → commit
  3. On failure: `git reset --hard HEAD`

**`PatchResult`:** commit_sha, commit_message, changes_patch_path

#### CLI integration

- Optional `--patch-file PATH` after branch creation
- Dev/testing path for validating patch workflow without LLM coder

#### Tests

- `tests/test_patches.py` — apply, commit message, export, failure recovery, idempotent retry

#### Bugfixes during review

- Commit before export so failed export leaves branch committed
- `--check` only on validation phase, not apply phase
- `rev-parse` inside existing try block

---

### Backlog #7 — Fetch issue metadata

**GitHub:** [#8](https://github.com/DarshanCode2005/Go-PR-Bot/issues/8)  
**Commit:** `a3c76d1` feat(github): fetch full IssueContext with comments and --force  
**PR:** [#50](https://github.com/DarshanCode2005/Go-PR-Bot/pull/50)

#### What was built

**`github_issues.py`**

Models:
- `IssueComment` — author, body, created_at
- `IssueContext` — repo, number, title, body, labels, state, comments
- `IssueContext.is_closed` property

Fetch:
- Primary: `gh issue view --json title,body,labels,state,comments`
- Fallback: PyGithub with `GITHUB_TOKEN`
- Comments capped to last N (`settings.max_issue_comments`, default 20)
- Raises `IssueFetchError` if neither path works

Closed issue handling:
- `ensure_issue_open_or_forced()` — raises `ClosedIssueError` unless `--force`
- CLI flag: `--force`

Artifacts:
- `write_issue_context()` → `issue_context.json`

Legacy:
- `fetch_issue_title()` still available; delegates to full fetch

#### Tests

- `tests/test_github_issues.py` — gh JSON parsing, comment cap, closed issue, `--force`, artifact write
- Fixture: `tests/fixtures/issue_view.json`

#### CLI integration

- Fetch runs before branch creation (title needed for slug)
- Exit 2 on closed issue without force; exit 1 on fetch failure

---

### Backlog #8 — Parse issue for symbols and scope hints

**GitHub:** [#9](https://github.com/DarshanCode2005/Go-PR-Bot/issues/9)  
**Commit:** `ee71ce6` feat(scope): extract issue scope hints and context builder stub  
**PR:** (pending / local)

#### What was built

**`issue_scope.py`**

- `extract_scope_hints(issue)` — regex/heuristic extraction from title + body + comments:
  - Backtick spans
  - `.go` file paths
  - `package` lines
  - `func` declarations
  - `github.com/...` import paths
  - `panic:` / `error:` substrings
  - Quoted error strings
- `_normalize_hint()` — strip noise, skip URLs and single-char tokens
- `_dedupe_preserve_order()` — case-insensitive dedupe, cap `MAX_SCOPE_HINTS = 50`
- `enrich_scope_hints_llm()` — optional LiteLLM call when API keys set; merge + dedupe; silent fallback on failure
- `build_scope_hints()` — public entry: heuristics then optional LLM

**`context_builder.py`** (stub for backlog #13 / GitHub #14)

- `ScopeBundle` — scope_hints, issue_number, repo, files (empty list placeholder)
- `prepare_scope(issue, settings)` → `ScopeBundle`
- `write_scope_hints(ctx, bundle)` → `scope_hints.json`

#### Key decisions

- Separate `scope_hints.json` artifact — does not modify `issue_context.json` schema
- Heuristics work offline in CI without API keys
- LLM enrichment is best-effort, non-blocking

#### Tests

Fixtures (`tests/fixtures/issue_bodies/`):
- `gin_router.md` — expects context.go, BindJSON, panic:
- `cobra_flags.md` — expects command.go, PersistentFlags, cmd
- `validator_error.md` — expects validator.go, import path, required

Test files:
- `tests/test_issue_scope.py` — 3 fixture tests, dedupe, no-LLM path, mocked LLM merge
- `tests/test_context_builder.py` — prepare_scope, write artifact

#### CLI integration

```python
scope_bundle = prepare_scope(issue_ctx, settings)
write_scope_hints(ctx, scope_bundle)
logger.info("Scope hints: %s", scope_bundle.scope_hints[:10])
```

Runs after `write_issue_context`, before `create_issue_branch`.

#### Out of scope (deferred)

- Ripgrep search (backlog #12 / GitHub #13)
- File ranking / `context_bundle.json` (backlog #13 / GitHub #14)
- Planner/coder agents

---

## Template for future issues

Copy this block when appending the next implemented issue.

---

### Backlog #N — {Title}

**GitHub:** [#M](https://github.com/DarshanCode2005/Go-PR-Bot/issues/M)  
**Commit:** `{hash}` {message}  
**PR:** [#P](https://github.com/DarshanCode2005/Go-PR-Bot/pull/P)

#### What was built

- …

#### Key decisions

- …

#### Tests

- …

#### CLI / pipeline integration

- …

#### Artifacts added/changed

- …

#### Dependencies on prior issues

- …

#### Out of scope

- …

#### Verification

```bash
pytest -q && ruff check src tests
```

---

## Quick reference: CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--repo` | required | `owner/name`, must be allowlisted |
| `--issue` | required | GitHub issue number |
| `--dry-run` | true | Skip push/PR (PR not implemented yet) |
| `--no-dry-run` | — | Required for future PR creation |
| `--create-pr` | false | Open draft PR via gh (not implemented yet) |
| `--patch-file` | None | Dev: apply unified diff and commit |
| `--force` | false | Proceed on closed issues |

---

## Environment variables

See `.env.example` and `config.py`. Minimum for current pipeline:

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` or `gh auth` | For real issue fetch | Issue metadata |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Optional | LLM scope enrichment only |
| `GO_AGENT_LOG_LEVEL` | Optional | DEBUG/INFO/WARNING/ERROR |

---

*Last updated: after Backlog #8 (GitHub #9) — scope hints and context builder stub.*
