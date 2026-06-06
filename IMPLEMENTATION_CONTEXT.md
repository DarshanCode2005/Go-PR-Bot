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
| #9 PR generator | [#10](https://github.com/DarshanCode2005/Go-PR-Bot/issues/10) | Done |
| #10 Create PR via gh | [#11](https://github.com/DarshanCode2005/Go-PR-Bot/issues/11) | Done |
| #11 Repo file tree | [#12](https://github.com/DarshanCode2005/Go-PR-Bot/issues/12) | Done |
| #12 Ripgrep search | [#13](https://github.com/DarshanCode2005/Go-PR-Bot/issues/13) | Done |
| #13 Context builder | [#14](https://github.com/DarshanCode2005/Go-PR-Bot/issues/14) | Done |
| #14 RAG retrieval | [#15](https://github.com/DarshanCode2005/Go-PR-Bot/issues/15) | Done |
| #15 LLM client wrapper | [#16](https://github.com/DarshanCode2005/Go-PR-Bot/issues/16) | Done |
| #16 Planner agent | [#17](https://github.com/DarshanCode2005/Go-PR-Bot/issues/17) | Done |
| #17 Coder agent | [#18](https://github.com/DarshanCode2005/Go-PR-Bot/issues/18) | Done |
| #18 Parallel coder | [#19](https://github.com/DarshanCode2005/Go-PR-Bot/issues/19) | Done |
| #19 Integrator | [#20](https://github.com/DarshanCode2005/Go-PR-Bot/issues/20) | Done |
| #20 LangGraph skeleton | [#21](https://github.com/DarshanCode2005/Go-PR-Bot/issues/21) | Done |
| #21 Wire Epic 4 nodes | [#22](https://github.com/DarshanCode2005/Go-PR-Bot/issues/22) | Done |
| #22 Subprocess test runner | [#23](https://github.com/DarshanCode2005/Go-PR-Bot/issues/23) | Done |

---

## Current pipeline state

`go-agent run` performs CLI setup (clone through context bundle and branch), then invokes the LangGraph **validation graph** (`plan → code → integrate → test → END`). Patches are applied locally; `test_result.json` is written; dry-run exits **0** on pass or **1** on test failure. Fix/review/pr nodes remain stubbed for later issues.

```
go-agent run --repo <owner/name> --issue <N>
  │
  ├─ create_run_context()          → UUID run_id, artifact + workspace dirs
  ├─ configure_run_logging()       → console + artifacts/{run_id}/run.log
  ├─ ensure_repo_cloned()          → workspaces/{run_id}/repo
  ├─ build_repo_map()              → file tree + go.mod + packages
  ├─ write_repo_map()              → repo_map.json
  ├─ fetch_issue_context()         → IssueContext via gh or PyGithub
  ├─ ensure_issue_open_or_forced() → blocks closed issues unless --force
  ├─ write_issue_context()         → issue_context.json
  ├─ prepare_scope() + ripgrep search     → scope_hints.json + search_hits.json
  ├─ [optional --rag] semantic retrieval  → rag_hits.json merged into search hits
  ├─ build_context_bundle()             → code_graph.json + context_bundle.json
  ├─ create_issue_branch()         → agent/issue-{N}-{slug}
  ├─ write_branch_meta()           → branch_meta.json
  ├─ LangGraph validation graph:
  │     plan_node → build_fix_plan + plan.json
  │     code_node → build_proposed_patch + proposed.patch + coder_meta.json
  │     integrate_node → integrate + apply_patch_and_commit → changes.patch
  │     test_node → run_tests + test_result.json
  ├─ build_pr_draft() + write_pr_md()    → PR.md
  ├─ [optional] maybe_create_pr()        → --no-dry-run --create-pr only
  │     push branch + gh pr create --draft → pr_meta.json, exit 0
  └─ dry-run exit 0 (exit 1 if tests fail)
```

**Approved repos:** `gin-gonic/gin`, `spf13/cobra`, `go-playground/validator`, `golangci/golangci-lint`

**Test suite:** 176 tests, `pytest -q && ruff check src tests`

---

## Artifacts per run

All artifacts live under `artifacts/{run_id}/`:

| File | Introduced by | Contents |
|------|---------------|----------|
| `run.log` | Backlog #3 | Structured run log with `run_id` in every line |
| `repo_meta.json` | Backlog #4 | repo, remote HEAD SHA, cache hit, paths |
| `repo_map.json` | Backlog #11 | File tree, go.mod module path, top-level packages |
| `issue_context.json` | Backlog #7 | Full `IssueContext` (title, body, labels, state, comments) |
| `scope_hints.json` | Backlog #8 | `ScopeBundle`: scope_hints, files (from search), issue_number, repo |
| `search_hits.json` | Backlog #12 | Ripgrep hits: path, line_number, line_text, query |
| `rag_hits.json` | Backlog #14 | Semantic RAG hits (when `--rag`); merged into bundle seeds |
| `code_graph.json` | Backlog #13 | In-memory code graph: nodes, edges, seeds |
| `context_bundle.json` | Backlog #13 | Ranked files with tiered content under char budget |
| `plan.json` | Backlog #16 | Structured fix plan: files, steps, test_commands, acceptance_criteria, optional `file_dependencies` |
| `proposed.patch` | Backlog #17 | Combined unified diff from coder (per-file LLM patches) |
| `coder_meta.json` | Backlog #17 | Per-file patch metadata, `execution_waves`, combined diff |
| `integrator_meta.json` | Backlog #19 | Conflict resolutions, `files_touched`, resolved patch |
| `test_result.json` | Backlog #22 | Subprocess test commands, exit codes, stdout/stderr |
| `resolved.patch` | Backlog #19 | Unified diff after sequential apply / merge |
| `branch_meta.json` | Backlog #5 | branch name, base SHA, default branch, issue info |
| `changes.patch` | Backlog #6 | `git diff` from base SHA (after patch apply) |
| `PR.md` | Backlog #9 | Draft PR title/body: Problem, Solution, Test plan, Fixes #N |
| `pr_meta.json` | Backlog #10 | Draft PR URL, title, branch (when `--create-pr`) |

Workspace clone: `workspaces/{run_id}/repo`

Shared cache: `workspaces/_cache/{owner__repo}/` (shallow clone + `meta.json` + optional `rag_index/{sha}/`)

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
| `llm_client.py` | Shared LiteLLM `complete()` with model tier routing and retries |
| `planner.py` | Strong-tier planner; Pydantic `FixPlan`; writes `plan.json` |
| `coder.py` | Fast-tier per-file coder; SEARCH/REPLACE → unified diff; scope guard |
| `integrator.py` | Sequential patch apply; LLM merge on overlapping hunks |
| `skills.py` | Skill loading; repo-specific test command override parsing |
| `test_runner.py` | Subprocess test command runner; writes `test_result.json` |
| `orchestrator/` | LangGraph graph; wired plan/code/integrate/test nodes + stub fix/review/pr |
| `orchestrator/runtime.py` | Reconstruct RunContext and Pydantic models from AgentState |
| `context_builder.py` | Code graph, ranked context bundle, scope enrichment |
| `pr_writer.py` | PR draft template + optional LLM; writes `PR.md` |
| `github_pr.py` | Push branch + `gh pr create --draft`; writes `pr_meta.json` |
| `repo_map.py` | Depth-limited file tree, go.mod parse, top-level packages |
| `repo_search.py` | Ripgrep wrapper; scope-hint batch search |

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

### Backlog #9 — PR title and body generator

**GitHub:** [#10](https://github.com/DarshanCode2005/Go-PR-Bot/issues/10)  
**Commit:** (pending) `feat(pr): generate PR.md draft from issue and optional patch (fixes #10)`  
**PR:** (pending)

#### What was built

**`pr_writer.py`**

- `PRDraft` — title, problem, solution, test_plan, issue_number, repo
- `build_pr_template()` — offline draft from issue + optional scope hints + patch:
  - Title via [`format_commit_message`](src/go_agent/patches.py) or existing commit message
  - Problem from issue title + first body paragraph
  - Solution from changed files in patch, scope hints, or placeholder
  - Test plan checklist with `go test ./... -count=1` and scoped command when hints allow
- `enrich_pr_llm()` — optional LiteLLM refinement when API keys set; silent fallback
- `build_pr_draft()` — template then optional LLM
- `render_pr_markdown()` — markdown with `# title`, `## Problem`, `## Solution`, `## Test plan`, `Fixes #N`
- `write_pr_md()` → `artifacts/{run_id}/PR.md`

#### Key decisions

- Template-first: works in dry-run and CI without API keys or network push
- Uses exported `changes.patch` content (not raw patch file) when `--patch-file` was applied
- Separate `PR.md` artifact; no `gh pr create` in this issue (Backlog #10 / GitHub #11)

#### Tests

- `tests/test_pr_writer.py` — template without LLM, patch file list, mocked LLM merge, section rendering, artifact write

#### CLI integration

Runs after optional `--patch-file`, before pipeline-not-implemented exit:

```python
pr_draft = build_pr_draft(issue_ctx, settings, scope_hints=..., patch_text=..., commit_message=...)
write_pr_md(ctx, pr_draft)
```

#### Dependencies on prior issues

- Backlog #7 — `IssueContext`
- Backlog #8 — `scope_hints` from `ScopeBundle`
- Backlog #6 — optional `changes.patch` / commit message via `--patch-file`

#### Out of scope

- `gh pr create` — Backlog #10 / GitHub #11
- LangGraph PR agent node
- `pr_summary.json` separate artifact

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #10 — Create PR via gh (optional path)

**GitHub:** [#11](https://github.com/DarshanCode2005/Go-PR-Bot/issues/11)  
**Commit:** (pending) `feat(github): create draft PR via gh when --create-pr (fixes #11)`  
**PR:** (pending)

#### What was built

**`github_pr.py`**

- `PRResult` — url, title, branch_name
- `count_commits_ahead()` — validates branch has commits beyond base SHA
- `push_branch()` — `git push -u origin {branch}`
- `create_draft_pr()` — `gh pr create --draft --repo ... --base ... --head ... --title ... --body ...`
- `write_pr_meta()` → `pr_meta.json`
- `maybe_create_pr()` — orchestrates push + create + meta artifact

**`pr_writer.py`**

- `render_pr_body()` — PR body without H1 title for `gh pr create`
- `render_pr_markdown()` refactored to compose title + body

#### Key decisions

- Gated by `not dry_run and create_pr` — dry-run never calls `gh` or push
- Requires commits ahead of base; clear error if branch is empty
- On success: echo PR URL to stdout, log, exit 0
- Push targets `origin` from clone; fork remote config out of scope

#### Tests

- `tests/test_github_pr.py` — commit count, push, gh URL parse, no commits error, dry-run skip, create-pr path
- `tests/test_pr_writer.py` — `render_pr_body` excludes title

#### CLI integration

After `write_pr_md`, when `--no-dry-run --create-pr`:

```python
pr_result = maybe_create_pr(repo_path, repo, branch, pr_draft, ctx, logger)
typer.echo(pr_result.url)
raise typer.Exit(code=0)
```

#### Dependencies on prior issues

- Backlog #9 — `PRDraft` and `PR.md`
- Backlog #5 — branch name and base SHA
- Backlog #6 — commits on branch (via patch or future agent)

#### Out of scope

- Fork/upstream remote configuration
- Non-draft PRs
- Push when `--no-dry-run` without `--create-pr`

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #11 — Repo file tree and go.mod summary

**GitHub:** [#12](https://github.com/DarshanCode2005/Go-PR-Bot/issues/12)  
**Commit:** (pending) `feat(repo): build repo_map.json with file tree and go.mod summary (fixes #12)`  
**PR:** (pending)

#### What was built

**`repo_map.py`**

- `TreeNode`, `GoModSummary`, `RepoMap` pydantic models
- `parse_go_mod()` — regex extract `module` and `go` lines
- `build_file_tree()` — depth-limited `os.scandir` walk; skips `.git` and optional `vendor`
- `list_top_level_packages()` — root dirs containing any `*.go` files
- `build_repo_map()` / `write_repo_map()` → `artifacts/{run_id}/repo_map.json`

**`config.py`**

- `repo_map_max_depth` (default 4)
- `repo_map_skip_vendor` (default True)

#### Key decisions

- Runs immediately after clone — no branch or issue fetch required
- No `go list` subprocess — hermetic tests without Go toolchain
- Symlinks not followed; dirs at max depth have empty children

#### Tests

- `tests/test_repo_map.py` — go.mod parse, depth limit, skip dirs, packages, artifact write

#### CLI integration

After `ensure_repo_cloned`:

```python
repo_map = build_repo_map(repo_path, repo, settings)
write_repo_map(ctx, repo_map)
```

#### Dependencies on prior issues

- Backlog #4 — cloned repo at `workspaces/{run_id}/repo`

#### Out of scope

- Ripgrep wrapper (Backlog #12 / GitHub #13)
- File ranking in context builder (Backlog #13 / GitHub #14)
- Tree size / token caps

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #12 — Ripgrep search tool

**GitHub:** [#13](https://github.com/DarshanCode2005/Go-PR-Bot/issues/13)  
**Commit:** (pending) `feat(search): add ripgrep wrapper and scope-hint search integration (fixes #13)`  
**PR:** (pending)

#### What was built

**`repo_search.py`**

- `SearchHit`, `SearchResponse` models
- `search_repo()` — `rg --fixed-strings --line-number --max-count` with configurable timeout
- `search_scope_hints()` — batch search deduped scope hints (max 10 queries)
- `RipgrepError` / `RipgrepNotFoundError`

**`config.py`**

- `ripgrep_timeout` (default 30s)
- `ripgrep_max_results` (default 50)
- `ripgrep_default_glob` (default `*.go`)
- Reuses `repo_map_skip_vendor` for `!vendor/**` and `!.git/**` globs

**`context_builder.py`**

- `build_scope_with_search()` — hints + ripgrep enrichment
- `enrich_scope_from_search()` — populates `ScopeBundle.files`; non-fatal on missing `rg`
- `write_search_hits()` → `search_hits.json`

#### Key decisions

- Literal (`--fixed-strings`) search for scope hints — safer than regex
- Missing `rg` or search failure logs warning and continues (pipeline usable in CI)
- Full file ranking deferred to Backlog #13 / GitHub #14

#### Tests

- `tests/test_repo_search.py` — line parse, hits, no match, timeout, truncated, dedupe
- `tests/test_context_builder.py` — `build_scope_with_search`, `write_search_hits`

#### CLI integration

After issue fetch:

```python
scope_bundle, search_hits = build_scope_with_search(issue_ctx, repo_path, settings, logger=logger)
write_scope_hints(ctx, scope_bundle)
write_search_hits(ctx, scope_bundle, search_hits)
```

#### Dependencies on prior issues

- Backlog #8 — `scope_hints` from issue text
- Backlog #11 — cloned repo at `repo_path`

#### Out of scope

- `context_bundle.json` file ranking (Backlog #13 / GitHub #14)
- MCP `repo_search` tool exposure
- Regex / multiline ripgrep modes

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #13 — Context builder (issue + search → bundle)

**GitHub:** [#14](https://github.com/DarshanCode2005/Go-PR-Bot/issues/14)  
**Commit:** (pending) `feat(context): lightweight code graph, ranked context bundle, tiered packing (fixes #14)`  
**PR:** (pending)

#### What was built

**`code_graph.py`**

- `GraphNode`, `GraphEdge`, `CodeGraph` models
- `build_code_graph()` — edges: `issue_hint`, `rg_hit`, `tests`, `in_package`, `imports`
- Regex import parsing mapped via `go.mod` module path
- Skips vendor, `.pb.go`, `_gen.go`, bindata files
- `write_code_graph()` → `code_graph.json`

**`context_ranker.py`**

- `RankedFile` model
- `rank_files()` — weighted BFS from graph seeds (100/70/40 by hop); +10 ripgrep boost
- Always injects paired `*_test.go` when a source `.go` file is ranked

**`context_builder.py`** (extended)

- `ContextFileEntry`, `ContextBundle` models
- `pack_context()` — tiered content: `full` → `summary` → `snippet` → `structural` with char budget downgrade
- Optional LiteLLM one-paragraph file summary (falls back to snippet)
- `build_context_bundle()` orchestrator; syncs `ScopeBundle.files` to ranked paths
- `write_context_bundle()` → `context_bundle.json`

**`config.py` / `.env.example`**

- `context_max_chars` (80000), `context_max_files` (15), `context_graph_max_hops` (2)
- `context_snippet_radius` (5), `context_full_file_top_k` (3), `context_summary_top_k` (5)

#### Key decisions

- Lightweight in-memory JSON graph (no Neo4j / `go/packages`) — Phase 1 per GitHub #14
- Greedy packing with tier downgrade when a file exceeds remaining budget
- Summary tier records actual tier when LLM unavailable (snippet fallback)

#### Tests

- `tests/test_code_graph.py` — test pairing, seeds, skip rules, artifact write
- `tests/test_context_ranker.py` — BFS distance, test pairing, max files cap
- `tests/test_context_builder.py` — budget packing, bundle build, LLM summary mock, artifact write

#### CLI integration

After search hits:

```python
code_graph, context_bundle = build_context_bundle(
    repo_path, issue_ctx, scope_bundle, search_hits, settings
)
write_code_graph(ctx, code_graph)
write_context_bundle(ctx, context_bundle)
```

#### Artifacts added/changed

- `code_graph.json` — nodes, edges, seeds
- `context_bundle.json` — ranked files with tier, rationale, content, char counts
- `scope_hints.json` / `search_hits.json` — `files` list synced to bundle paths

#### Dependencies on prior issues

- Backlog #8 — `scope_hints`
- Backlog #11 — cloned repo, `go.mod` module path
- Backlog #12 — `search_hits` for seeds and snippet line numbers

#### Out of scope

- Neo4j / cross-run graph persistence
- `go/packages` import analysis
- Planner/coder agent consumption of bundle (later)
- Embeddings RAG (moved to Backlog #14 — done)

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #14 — Optional RAG for context retrieval

**GitHub:** [#15](https://github.com/DarshanCode2005/Go-PR-Bot/issues/15)  
**Commit:** (pending) `feat(rag): optional ChromaDB semantic retrieval with offline embedder (fixes #15)`  
**PR:** (pending)

#### What was built

**`repo_rag.py`**

- `RagChunk`, `RagHit`, `RagArtifact` models
- `chunk_go_files()` — overlapping line windows; same skip rules as code graph
- `get_or_build_index()` — ChromaDB persistent index cached at `workspaces/_cache/{repo}/rag_index/{sha[:12]}/`
- `retrieve_chunks()` / `retrieve_rag_hits()` — top-k semantic search for issue query
- `rag_hits_to_search_hits()` + `merge_search_hits()` — feed existing graph/ranker pipeline
- `write_rag_hits()` → `rag_hits.json`

**Embed providers**

- **Local (offline):** `sentence-transformers` (`all-MiniLM-L6-v2` default) via `pip install -e ".[rag]"`
- **API:** OpenAI embeddings via LiteLLM when `rag_embed_provider=openai`

**`config.py` / `.env.example` / `cli.py`**

- `enable_rag`, `rag_top_k`, `rag_chunk_lines`, `rag_chunk_overlap`, `rag_embed_provider`, `rag_embed_model`, `rag_min_score`
- `--rag/--no-rag` CLI flag (default off); `GO_AGENT_ENABLE_RAG` env mirror

**`context_ranker.py`**

- Rationale `"semantic retrieval"` for hits with `rag:` query prefix

#### Key decisions

- ChromaDB embedded (already in `pyproject.toml` optional extra) over Qdrant — simpler offline setup
- RAG supplements ripgrep; default path unchanged when disabled
- Missing deps or failures log warning and fall back to ripgrep-only
- Index cached per repo SHA to avoid re-embedding on repeat runs

#### Tests

- `tests/test_repo_rag.py` — chunking, adapter, merge, disabled/missing-deps fallback, artifact write
- `tests/test_context_builder.py` — RAG hits seed bundle with semantic rationale
- `tests/test_cli.py` — `--rag` in help

#### CLI integration

After ripgrep search, before `build_context_bundle`:

```python
rag_hits = retrieve_rag_hits(repo_path, issue_ctx, repo, settings, logger=logger)
if settings.enable_rag:
    write_rag_hits(ctx, issue_ctx, rag_query, rag_hits)
    search_hits = merge_search_hits(search_hits, rag_hits_to_search_hits(rag_hits))
```

#### Dependencies on prior issues

- Backlog #12 — ripgrep `SearchHit` contract
- Backlog #13 — graph seeds and context bundle consume merged hits

#### Out of scope

- Qdrant / Neo4j vector store
- AST-aware Go chunking
- Cross-issue Mem0 memory
- Direct agent-loop RAG consumption (bundle remains contract)

#### Verification

```bash
pytest -q && ruff check src tests
pip install -e ".[rag]"   # for local embeddings
go-agent run --repo gin-gonic/gin --issue 1 --dry-run --rag
```

---

### Backlog #15 — Unified LiteLLM client

**GitHub:** [#16](https://github.com/DarshanCode2005/Go-PR-Bot/issues/16)  
**Commit:** (pending) `feat(llm): unified LiteLLM complete() with tier routing and rate-limit retry (fixes #16)`  
**PR:** (pending)

#### What was built

**`llm_client.py`**

- `complete(messages, tier, settings)` centralized LLM completion entry
- Tier routing: `fast` → `model_fast`, `strong` → `model_strong`
- Retry loop for rate limits with exponential backoff
- Injectable `CompletionTransport` via `set_completion_transport()` for tests

**Call-site refactor**

- `issue_scope.py` now uses `complete(...)` in `enrich_scope_hints_llm`
- `context_builder.py` now uses `complete(...)` in `_summarize_file`
- `pr_writer.py` now uses `complete(...)` in `enrich_pr_llm`

**Config / env**

- Added `llm_max_retries` (default `3`)
- Added `llm_retry_base_delay` (default `1.0`)
- Documented in `.env.example`

#### Key decisions

- Keep caller behavior unchanged: failures return `None` and existing fallbacks stay active
- Limit retry logic to rate-limit shaped failures only
- Leave `repo_rag.py` embedding path out of scope for this backlog

#### Tests

- New `tests/test_llm_client.py` for tier routing, retries, exhausted retries, and transport injection
- Added transport-based integration tests in `tests/test_issue_scope.py` and `tests/test_pr_writer.py`

#### Dependencies on prior issues

- Backlog #3/#4 settings infrastructure (`model_fast`, `model_strong`, env loading)
- Backlog #8 and #9 optional LLM enrichers consumed the new client

#### Out of scope

- `litellm.embedding` in `repo_rag.py`
- Streaming/tool-call support for LLM responses
- Agent-loop usage of `strong` tier (future backlog)

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #16 — Planner agent (structured plan.json)

**GitHub:** [#17](https://github.com/DarshanCode2005/Go-PR-Bot/issues/17)  
**Commit:** (pending) `feat(planner): structured fix plan with Pydantic validation and plan.json artifact (fixes #17)`  
**PR:** (pending)

#### What was built

**`planner.py`**

- `FixPlan` Pydantic model: `files`, `steps`, `test_commands`, `acceptance_criteria`
- Field validators: non-empty lists, deduped files, at least one `go test` command
- `build_planner_messages()` — issue + scope hints + context bundle excerpts + repo skill
- `build_fix_plan()` — `complete(..., tier="strong")`; JSON parse + validate; one retry on failure
- `write_plan()` → `plan.json`
- `PlanError` — run fails when planner cannot complete (integral step)

**`cli.py`**

- Planner runs after context bundle, before branch creation
- `PlanError` caught with exit code 1

**`tests/helpers.py`**

- `enable_planner_mock()` — mock transport for CLI integration tests

#### Key decisions

- Planner is mandatory: no heuristic fallback when LLM/validation fails
- First production use of `model_strong` tier
- Repo skills loaded from `skills/{owner__repo}/SKILL.md` with `_default` fallback

#### Tests

- `tests/test_planner.py` — validation, API key required, parse, retry, strong tier, artifact
- CLI integration tests updated with `enable_planner_mock()`

#### CLI integration

```python
fix_plan = build_fix_plan(issue_ctx, context_bundle, scope_bundle.scope_hints, settings, logger=logger)
write_plan(ctx, fix_plan)
```

#### Dependencies on prior issues

- Backlog #13 — `context_bundle.json` input
- Backlog #15 — `llm_client.complete()` with strong tier

#### Out of scope

- LangGraph orchestrator node wiring
- Coder/reviewer consumption of `plan.json`

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #17 — Coder agent (per-file patch generation)

**GitHub:** [#18](https://github.com/DarshanCode2005/Go-PR-Bot/issues/18)  
**Commit:** (pending) `feat(coder): per-file patch generation from plan with scope guard (fixes #18)`  
**PR:** (pending)

#### What was built

**`coder.py`**

- `PlanSlice`, `FilePatch`, `CoderArtifact` Pydantic models
- Search-replace parser/applier (`--- SEARCH` / `+++ REPLACE`)
- `unified_diff_for_file()` via `git diff --no-index` for git-apply-compatible output
- `validate_patch_scope()` — refuses paths outside `plan.files`
- `generate_file_patch()` — fast-tier LLM per file, one retry on parse/apply failure
- `build_proposed_patch()` — loops `plan.files`, merges into `combined_patch`
- `write_coder_artifact()` → `proposed.patch` + `coder_meta.json`
- `CoderError` — run fails when coder cannot complete

**`cli.py`**

- Coder runs after branch when `--patch-file` is not set
- Writes artifacts, applies patch via `apply_patch_and_commit()`
- `CoderError` and patch apply failures exit code 1
- `--patch-file` dev path unchanged (skips coder)

**`config.py`**

- `coder_max_file_chars` (default 60000)

**`tests/helpers.py`**

- `enable_agent_mocks()` — dispatches mock LLM responses for planner, scope, summary, and coder
- `enable_planner_mock()` alias retained

#### Key decisions

- One LLM call per `plan.files` entry (plan slice + bundle excerpt + on-disk source)
- Primary LLM output format: SEARCH/REPLACE blocks; unified diff accepted as fallback
- Missing planned files fail the run (integral consistency with planner)
- Fast tier sufficient for single-file edits

#### Tests

- `tests/test_coder.py` — parser, applier, scope guard, mock transport, git apply --check, artifacts
- CLI integration tests updated with `enable_agent_mocks()` and bare-repo fixtures

#### CLI integration

```python
if patch_file is None:
    coder_artifact = build_proposed_patch(repo_path, issue_ctx, fix_plan, context_bundle, settings, logger=logger)
    write_coder_artifact(ctx, coder_artifact)
    result = apply_patch_and_commit(repo_path, ctx, coder_artifact.combined_patch, ...)
```

#### Dependencies on prior issues

- Backlog #16 — `plan.json` / `FixPlan`
- Backlog #13 — `context_bundle.json`
- Backlog #15 — `llm_client.complete()` fast tier
- Backlog #6 — `patches.apply_patch_and_commit()`

#### Out of scope

- LangGraph parallel file workers
- Multi-file edits in a single LLM call
- Auto-fix loop on `git apply` failure

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #18 — Parallel coder orchestration

**GitHub:** [#19](https://github.com/DarshanCode2005/Go-PR-Bot/issues/19)  
**Commit:** (pending) `feat(coder): parallel per-file coding with depends_on wave scheduling (fixes #19)`  
**PR:** (pending)

#### What was built

**`planner.py`**

- `FixPlan.file_dependencies: dict[str, list[str]]` — optional per-file `depends_on`
- Planner prompt documents optional `file_dependencies` key
- Validators: unknown paths, self-deps, and cycles rejected at plan parse time

**`coder.py`**

- `schedule_coder_waves()` — topological waves via Kahn's algorithm
- `build_proposed_patch()` — runs each wave in parallel via `ThreadPoolExecutor` (`coder_max_workers`)
- Dependent files run in later waves; disjoint files in the same wave run concurrently
- `_dependency_context_for_file()` — injects upstream files' post-patch content into dependent file prompts
- `CoderArtifact.execution_waves` recorded in `coder_meta.json`

**`config.py`**

- `coder_max_workers` (default `4`)

#### Key decisions

- Thread pool over asyncio — sync `complete()` stays unchanged
- Cross-file dependency context passed in LLM prompt, not applied to target file content
- Empty/missing `file_dependencies` → single parallel wave (backward compatible)

#### Tests

- `tests/test_planner.py` — dependency validation and cycle rejection
- `tests/test_coder.py` — wave scheduling, parallel concurrency, sequential order, dependency overlay

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #19 — Integrator / conflict resolution pass

**GitHub:** [#20](https://github.com/DarshanCode2005/Go-PR-Bot/issues/20)  
**Commit:** (pending) `feat(integrator): sequential patch apply with LLM conflict merge (fixes #20)`  
**PR:** (pending)

#### What was built

**`integrator.py`**

- `integrate_file_patches()` — apply `FilePatch` list in plan order on clean worktree at `base_sha`
- On `git apply` failure for a path, gather all patches for that file and call `merge_patches_with_llm()` once (fast tier, retry)
- `_dependency_context`-style merge prompt: original file + all conflicting hunks
- Reuses coder SEARCH/REPLACE parser and `normalize_llm_patch()`
- `IntegratorResult` with `resolved_patch`, `conflicts`, `files_touched`
- `write_integrator_artifact()` → `integrator_meta.json` + `resolved.patch`
- Resets worktree to `base_sha` after exporting diff (idempotent final apply)

**`cli.py`**

- After coder artifacts: `integrate_file_patches()` → `write_integrator_artifact()` → `apply_patch_and_commit(resolved_patch)`
- `IntegratorError` → exit 1; `--patch-file` skips integrator

**`config.py`**

- `integrator_max_merge_retries` (default `1`)

#### Key decisions

- Sequential apply detects real `git apply` conflicts (overlapping hunks on same file)
- Merge LLM sees base content plus every conflicting patch body
- Multiple patches per path preserved in `_order_file_patches()` (no dict overwrite)

#### Tests

- `tests/test_integrator.py` — disjoint apply, overlapping hunks merge (acceptance), merge prompt content, merge failure, artifacts

#### Verification

```bash
pytest -q && ruff check src tests
```

---

### Backlog #20 — LangGraph AgentState and graph skeleton

**GitHub:** [#21](https://github.com/DarshanCode2005/Go-PR-Bot/issues/21)  
**Commit:** (pending) `feat(orchestrator): LangGraph AgentState and stub graph (fixes #21)`  
**PR:** (pending)

#### What was built

**`orchestrator/state.py`**

- `AgentState` TypedDict for LangGraph channel schema
- Pydantic `TestResult` and `ReviewResult` sub-models (serialized into state dicts)

**`orchestrator/nodes.py`**

- Stub nodes: `plan_node`, `code_node`, `test_node`, `fix_node`, `review_node`, `pr_node`
- Partial state updates only; no calls to planner/coder/integrator yet
- Default `test_node` marks tests passed; pre-set `test_result` preserved for routing tests

**`orchestrator/graph.py`**

- `GRAPH_NODE_NAMES`, `build_graph()`, `compile_graph()`, `route_after_test()`
- Edges: plan → code → test → (fix | review) → pr → END; fix → code loop
- Fix cap from `max_fix_iterations` (settings or override)

**`docs/ARCHITECTURE.md`**

- Restored from git; added “LangGraph orchestrator (code)” section with Mermaid matching graph topology
- “Closed-loop state machine” distinguishes conceptual full system vs implemented stub

#### Key decisions

- TypedDict top-level state + Pydantic nested models (matches rest of codebase)
- Six stub nodes only (no separate `integrate` / `lint` in graph until later issues)
- Failed tests after max iterations route to `review` with `status="failed"` (escape hatch)

#### Tests

- `tests/test_orchestrator.py` — compile, node/edge introspection, routing, invoke happy path, fix loop, doc parity

#### CLI / pipeline integration

- None — CLI remains imperative; graph compiled via `go_agent.orchestrator.compile_graph()`

#### Dependencies on prior issues

- Backlog #17 — coder pipeline exists (graph `code` node stub only)
- Backlog #3 — `max_fix_iterations` in settings

#### Out of scope

- Wiring `cli.py` to `compile_graph().invoke()`
- Real planner/coder/integrator/test subprocess inside nodes
- LangGraph checkpointer

#### Verification

```bash
pytest tests/test_orchestrator.py -q
pytest -q && ruff check src tests
```

---

### Backlog #21 — Wire planner → coder → integrator nodes

**GitHub:** [#22](https://github.com/DarshanCode2005/Go-PR-Bot/issues/22)  
**Commit:** (pending) `feat(orchestrator): wire plan/code/integrate nodes and CLI implement graph (fixes #22)`  
**PR:** (pending)

#### What was built

**`orchestrator/runtime.py`**

- `run_context_from_state()`, `issue_from_state()`, `bundle_from_state()`, `plan_from_state()`, `branch_base_sha()`, `coder_artifact_from_state()`

**`orchestrator/nodes.py`**

- `plan_node` → `build_fix_plan` + `write_plan`
- `code_node` → `build_proposed_patch` + `write_coder_artifact`
- `integrate_node` → `integrate_file_patches` + `write_integrator_artifact` + `apply_patch_and_commit`
- `test`/`fix`/`review`/`pr` remain stubs

**`orchestrator/graph.py`**

- `IMPLEMENT_NODE_NAMES = (plan, code, integrate)`
- `compile_graph(implement_only=True)` — default CLI path ends at END after integrate
- `compile_graph(implement_only=False)` — full graph with integrate → test → fix loop

**`cli.py`**

- Setup through branch pre-graph; invokes implement graph; dry-run exits 0 after PR draft
- `--patch-file` dev path unchanged (skips graph)

#### Key decisions

- CLI keeps clone/issue/context/branch outside graph; Epic 4 agents run inside graph nodes
- Implement graph ENDs after integrate (test/review/pr deferred)
- AgentState extended with artifact paths, serialized context, branch_meta, patch results

#### Tests

- `tests/test_orchestrator.py` — implement vs full graph, wired invoke with mocks, E2E graph invoke with agent mocks
- `tests/test_cli.py`, `tests/test_cli_branch.py`, `tests/test_run_context.py` — dry-run exit 0, `changes.patch` present
- `tests/helpers.py` — `init_git_repo()` helper

#### Out of scope

- Subprocess test/lint nodes
- Review agent wiring
- LangGraph checkpointer

#### Verification

```bash
pytest tests/test_orchestrator.py tests/test_cli.py tests/test_cli_branch.py -q
pytest -q && ruff check src tests
```

---

### Backlog #22 — Subprocess test runner

**GitHub:** [#23](https://github.com/DarshanCode2005/Go-PR-Bot/issues/23)  
**Commit:** (pending) `feat(test): subprocess test runner with skill overrides (fixes #23)`  
**PR:** (pending)

#### What was built

**`skills.py`**

- `load_skill_text()`, `parse_skill_test_commands()`, `resolve_test_commands()`
- Repo skill YAML frontmatter or bash-block override; plan commands as fallback

**`test_runner.py`**

- `run_test_commands()`, `run_tests()`, `write_test_result()` → `test_result.json`
- `CommandResult` / `TestRunResult` Pydantic models; timeout via `TestRunnerError`

**`orchestrator/nodes.py`**

- `test_node` wired to `run_tests` + artifact export

**`orchestrator/graph.py`**

- `VALIDATION_NODE_NAMES`, `include_test` / `include_closed_loop` graph modes
- CLI default: `integrate → test → END`

**`config.py`**

- `test_timeout` (default 300, `GO_AGENT_TEST_TIMEOUT`)

**`cli.py`**

- `compile_graph(include_test=True)`; exit 1 on test failure

**`skills/gin-gonic__gin/SKILL.md`**

- Frontmatter `test_commands` override for acceptance demo

#### Key decisions

- Skill override applies only from repo-specific skill (not `_default`)
- Run all commands; pass only if all exit 0
- PR draft still written before exit 1 on test failure

#### Tests

- `tests/test_test_runner.py` — resolution, subprocess mock, timeout, artifact
- Updated orchestrator/CLI tests for validation graph and `test_result.json`
- `enable_agent_mocks()` patches `run_tests` for offline CLI tests

#### Out of scope

- Fix loop on test failure (Backlog #24)
- Lint/vet runner (Backlog #23 / GitHub #24)

#### Verification

```bash
pytest tests/test_test_runner.py tests/test_orchestrator.py tests/test_cli.py -q
pytest -q && ruff check src tests
```

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
| `--dry-run` | true | Skip push and `gh pr create` (default) |
| `--no-dry-run` | — | Required with `--create-pr` |
| `--create-pr` | false | Push branch and open draft PR via gh |
| `--patch-file` | None | Dev: apply unified diff and commit |
| `--force` | false | Proceed on closed issues |
| `--rag` | false | Enable semantic RAG retrieval (`pip install -e ".[rag]"`) |

---

## Environment variables

See `.env.example` and `config.py`. Minimum for current pipeline:

| Variable | Required | Purpose |
|----------|----------|---------|
| `GITHUB_TOKEN` or `gh auth` | For real issue fetch | Issue metadata |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Optional | LLM scope + PR draft enrichment |
| `GO_AGENT_LOG_LEVEL` | Optional | DEBUG/INFO/WARNING/ERROR |
| `GO_AGENT_CONTEXT_MAX_CHARS` | Optional | Context bundle char budget (default 80000) |
| `GO_AGENT_CONTEXT_MAX_FILES` | Optional | Max ranked files in bundle (default 15) |
| `GO_AGENT_ENABLE_RAG` | Optional | Enable semantic retrieval (default false) |
| `GO_AGENT_RAG_EMBED_PROVIDER` | Optional | `local` or `openai` (default local) |
| `GO_AGENT_LLM_MAX_RETRIES` | Optional | Rate-limit retry attempts for LLM completion |
| `GO_AGENT_LLM_RETRY_BASE_DELAY` | Optional | Base delay seconds for LLM retry backoff |

---

*Last updated: after Backlog #19 (GitHub #20) — integrator conflict merge pass.*
