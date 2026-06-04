# Implementation backlog → GitHub issues

Copy each section into a GitHub issue. Suggested labels: `epic`, `phase-0` … `phase-8`, `good-first-issue`, `agent`, `infra`, `mcp`, `docs`.

**Suggested milestone order:** M0 → M1 → … → M8. Issues within a phase can often be parallelized where no dependency is listed.

---

## Epic 0: Repository bootstrap

### Issue #1 — Initialize Python package and tooling
**Labels:** `phase-0`, `good-first-issue`  
**Depends on:** —

**Description:** Create `pyproject.toml` with package name `go_agent`, Python 3.11+, ruff, pytest. Add `src/go_agent/__init__.py`, `.gitignore`, `.env.example` (`OPENAI_API_KEY`, `GITHUB_TOKEN`, `WORK_DIR`).

**Acceptance criteria:**
- [ ] `pip install -e ".[dev]"` works
- [ ] `pytest` runs (empty suite OK)
- [ ] `ruff check` passes

---

### Issue #2 — Typer CLI skeleton
**Labels:** `phase-0`  
**Depends on:** #1

**Description:** `go-agent` entry with commands: `run`, `version`. `run` accepts `--repo`, `--issue`, `--dry-run`, `--create-pr`.

**Acceptance criteria:**
- [ ] `go-agent --help` documents all flags
- [ ] Missing required args exit with clear error

---

### Issue #3 — Configuration and logging
**Labels:** `phase-0`  
**Depends on:** #1

**Description:** Pydantic settings from env; structured logging to console + `artifacts/<run_id>/run.log`.

**Acceptance criteria:**
- [ ] Each run gets UUID `run_id`
- [ ] Log level configurable via env

---

## Epic 1: Workspace and Git

### Issue #4 — Clone approved repo into workspace
**Labels:** `phase-1`, `infra`  
**Depends on:** #3

**Description:** Shallow clone `owner/repo` into `workspaces/{run_id}/repo`. Allowlist the four approved repos.

**Acceptance criteria:**
- [ ] Invalid repo rejected
- [ ] Re-run uses existing clone if hash matches (optional cache)

---

### Issue #5 — Create issue branch
**Labels:** `phase-1`  
**Depends on:** #4

**Description:** Branch `agent/issue-{number}-{short-slug}` from default branch; record base SHA in state.

**Acceptance criteria:**
- [ ] Branch exists locally
- [ ] Slug derived from issue title (sanitized)

---

### Issue #6 — Apply patches and commit helper
**Labels:** `phase-1`  
**Depends on:** #5

**Description:** Utilities: apply unified diff, `git add`, `git commit` with message template. Export final `git diff` to artifacts.

**Acceptance criteria:**
- [ ] Failed patch apply returns actionable error
- [ ] `changes.patch` written to artifacts

---

## Epic 2: GitHub integration

### Issue #7 — Fetch issue metadata
**Labels:** `phase-2`  
**Depends on:** #3

**Description:** Load issue title, body, labels, state, comments (paginated, cap N). Support `gh issue view` fallback.

**Acceptance criteria:**
- [ ] Parsed into `IssueContext` pydantic model
- [ ] Closed issues warn but can proceed with `--force`

---

### Issue #8 — Parse issue for symbols and scope hints
**Labels:** `phase-2`, `agent`  
**Depends on:** #7

**Description:** Heuristic + LLM-light extract: mentioned file paths, func names, error strings, `package` keywords from body.

**Acceptance criteria:**
- [ ] Output `scope_hints: list[str]` used by context builder
- [ ] Unit tests with 3 sample issue bodies (fixtures)

---

### Issue #9 — PR title and body generator
**Labels:** `phase-2`, `agent`  
**Depends on:** #6, #7

**Description:** Template + LLM fills: Problem, Solution, Test plan, `Fixes #N`. Write `artifacts/.../PR.md`.

**Acceptance criteria:**
- [ ] PR.md matches assignment expectations
- [ ] Works in dry-run without network push

---

### Issue #10 — Create PR via gh (optional path)
**Labels:** `phase-2`  
**Depends on:** #9, #6

**Description:** `gh pr create --draft` when `--create-pr`; otherwise skip.

**Acceptance criteria:**
- [ ] Dry-run never calls gh
- [ ] Returns PR URL on success

---

## Epic 3: Repository understanding

### Issue #11 — Repo file tree and go.mod summary
**Labels:** `phase-3`  
**Depends on:** #4

**Description:** Build tree (depth limit), parse `go.mod` / module path, list top-level packages.

**Acceptance criteria:**
- [ ] `repo_map.json` in artifacts
- [ ] Ignores `.git`, `vendor` optional

---

### Issue #12 — Ripgrep search tool
**Labels:** `phase-3`  
**Depends on:** #11

**Description:** Wrapper: query, glob, max results → snippets with paths and line numbers.

**Acceptance criteria:**
- [ ] Used by context builder and agents
- [ ] Timeout configurable

---

### Issue #13 — Context builder (issue + search → bundle)
**Labels:** `phase-3`, `agent`  
**Depends on:** #8, #12

**Description:** Rank files: issue hints, search hits, neighbors (same dir, `_test.go`). Cap total tokens/chars.

**Acceptance criteria:**
- [ ] `context_bundle.json` lists files with rationale
- [ ] Includes related `*_test.go` when source file selected

---

### Issue #14 — Optional: local embeddings index (RAG)
**Labels:** `phase-3`, `enhancement`  
**Depends on:** #13

**Description:** Chunk Go files; embed with chosen provider; retrieve top-k for issue query. Feature-flag `--rag`.

**Acceptance criteria:**
- [ ] Works offline with local embedder OR documented API key
- [ ] Fallback to ripgrep-only when disabled

---

## Epic 4: LLM and agent prompts

### Issue #15 — LiteLLM provider abstraction
**Labels:** `phase-4`  
**Depends on:** #3

**Description:** Single `complete(messages, model_tier)` with tiers: `fast`, `strong`. Retry on rate limit.

**Acceptance criteria:**
- [ ] OpenAI and Anthropic documented in README
- [ ] Mock transport for tests

---

### Issue #16 — Planner agent
**Labels:** `phase-4`, `agent`  
**Depends on:** #13, #15

**Description:** System prompt + structured output: files, steps, test_commands, acceptance_criteria.

**Acceptance criteria:**
- [ ] Valid JSON schema enforced (pydantic)
- [ ] `plan.json` saved to artifacts

---

### Issue #17 — Coder agent (single-file)
**Labels:** `phase-4`, `agent`  
**Depends on:** #16, #12

**Description:** Given one file + plan slice, emit patch (search-replace blocks or unified diff). No unrelated files.

**Acceptance criteria:**
- [ ] Patch applies cleanly on sample fixture repo
- [ ] Refuses to edit files outside plan

---

### Issue #18 — Parallel coder orchestration
**Labels:** `phase-4`, `agent`  
**Depends on:** #17

**Description:** `asyncio` or thread pool: one coder per planned file; collect patches.

**Acceptance criteria:**
- [ ] Disjoint files run in parallel
- [ ] Dependent files run sequentially (planner marks `depends_on`)

---

### Issue #19 — Integrator / conflict resolution pass
**Labels:** `phase-4`, `agent`  
**Depends on:** #18, #6

**Description:** Apply patches in order; on conflict, one “merge” LLM call with both hunks + file content.

**Acceptance criteria:**
- [ ] Integration test with overlapping hunks fixture

---

## Epic 5: LangGraph orchestrator (closed loop)

### Issue #20 — Define AgentState and graph skeleton
**Labels:** `phase-5`, `agent`  
**Depends on:** #16

**Description:** TypedDict/pydantic state; nodes as stubs: plan, code, test, fix, review, pr.

**Acceptance criteria:**
- [ ] Graph compiles
- [ ] Mermaid diagram in ARCHITECTURE matches code

---

### Issue #21 — Wire planner → coder → integrator nodes
**Labels:** `phase-5`  
**Depends on:** #20, #19

**Description:** Implement node functions calling agents from Epic 4.

**Acceptance criteria:**
- [ ] End-to-end dry-run stops after integrate with patches applied locally

---

### Issue #22 — Subprocess test runner
**Labels:** `phase-5`, `infra`  
**Depends on:** #4

**Description:** Run `go test` (scoped or `./...`), timeout, capture exit code + output. Read `test_commands` from plan.

**Acceptance criteria:**
- [ ] `test_result.json` in artifacts
- [ ] Supports repo-specific command override from skills

---

### Issue #23 — Subprocess lint / vet runner
**Labels:** `phase-5`  
**Depends on:** #22

**Description:** `go vet`, optional `golangci-lint` if binary present; skill can define custom lint cmd.

**Acceptance criteria:**
- [ ] Lint failures include file:line when available

---

### Issue #24 — Fix agent and conditional edges
**Labels:** `phase-5`, `agent`  
**Depends on:** #21, #22, #23, #17

**Description:** On test/lint fail, feed errors to fix agent → new patch → integrator. Max iterations (default 5).

**Acceptance criteria:**
- [ ] Loop terminates with `failed` status when max exceeded
- [ ] Iteration count in state and logs

---

### Issue #25 — LangGraph checkpointer (run resume)
**Labels:** `phase-5`, `enhancement`  
**Depends on:** #20

**Description:** SqliteSaver under `artifacts/checkpoints/`; `go-agent resume --run-id`.

**Acceptance criteria:**
- [ ] Resume continues from last node
- [ ] Documented in README

---

## Epic 6: Review agent

### Issue #26 — Review rubric and prompt
**Labels:** `phase-6`, `agent`  
**Depends on:** #15

**Description:** Checklist: issue AC, tests, API breaks, style, error messages. Output structured review.

**Acceptance criteria:**
- [ ] `review.json` with `decision` and `comments[]`

---

### Issue #27 — Static review tools in review node
**Labels:** `phase-6`  
**Depends on:** #23, #26

**Description:** Attach `gofmt -d`, vet output to review context before LLM.

**Acceptance criteria:**
- [ ] Reviewer cites concrete lint/format issues

---

### Issue #28 — Review → fix → re-validate loop
**Labels:** `phase-6`  
**Depends on:** #24, #26

**Description:** If `request_changes`, one fix cycle then re-test (configurable max review rounds).

**Acceptance criteria:**
- [ ] Approve path goes to PR node
- [ ] Second failure escalates to `failed` with review attached

---

## Epic 7: Skills and MCP

### Issue #29 — Skill loader
**Labels:** `phase-7`  
**Depends on:** #3

**Description:** Load `skills/<repo>/SKILL.md` by repo name; inject into planner, coder, reviewer.

**Acceptance criteria:**
- [ ] Unknown repo uses `skills/_default/SKILL.md`

---

### Issue #30 — Skill: gin-gonic/gin
**Labels:** `phase-7`, `docs`  
**Depends on:** #29

**Description:** Test commands, middleware patterns, contribution notes from upstream README.

---

### Issue #31 — Skill: spf13/cobra
**Labels:** `phase-7`  
**Depends on:** #29

---

### Issue #32 — Skill: go-playground/validator
**Labels:** `phase-7`  
**Depends on:** #29

---

### Issue #33 — Skill: golangci/golangci-lint
**Labels:** `phase-7`  
**Depends on:** #29

---

### Issue #34 — MCP server: repo tools
**Labels:** `phase-7`, `mcp`  
**Depends on:** #12, #11

**Description:** `repo_search`, `repo_read`, `repo_map` MCP tools.

**Acceptance criteria:**
- [ ] Cursor MCP config snippet in README
- [ ] `python -m mcp.server` starts STDIO server

---

### Issue #35 — MCP server: validation and GitHub tools
**Labels:** `phase-7`, `mcp`  
**Depends on:** #22, #7

**Description:** `run_go_test`, `run_lint`, `github_get_issue`.

---

## Epic 8: End-to-end UX and submission

### Issue #36 — `go-agent run` end-to-end command
**Labels:** `phase-8`  
**Depends on:** #24, #28, #9, #10

**Description:** Single command runs full graph; prints summary + artifact paths.

**Acceptance criteria:**
- [ ] Documented example issue in README
- [ ] Dry-run produces plan, patch, test log, PR.md

---

### Issue #37 — Sample artifacts for evaluators
**Labels:** `phase-8`, `docs`  
**Depends on:** #36

**Description:** `samples/gin-issue-XXXX/` with redacted plan, patch, PR.md (from a real small issue).

---

### Issue #38 — Comparison helper vs known PR (dev tool)
**Labels:** `phase-8`, `enhancement`  
**Depends on:** #36

**Description:** Script: given issue with merged PR, diff agent patch vs `gh pr diff` file overlap stats.

**Acceptance criteria:**
- [ ] Prints file overlap % and line similarity hint

---

### Issue #39 — CI: lint and test this repo
**Labels:** `phase-8`  
**Depends on:** #1

**Description:** GitHub Actions: ruff, pytest on `go_agent` (mock LLM in CI).

---

### Issue #40 — README submission polish
**Labels:** `phase-8`, `docs`  
**Depends on:** #36

**Description:** Setup, env vars, architecture link, limitations, cost/token notes, approved repos list.

---

## Optional / post-MVP

| Issue | Title | Notes |
|-------|--------|------|
| #41 | Mem0 integration for cross-issue memory | Only after #36 stable |
| #42 | CrewAI adapter layer | Wrap same tools if you want Crew API |
| #43 | Web UI for run status | Nice-to-have |
| #44 | Docker compose one-shot runner | For evaluators without local Go |

---

## Dependency graph (critical path)

```
#1 → #2,#3 → #4 → #5 → #6
              ↘ #7 → #8 → #13 → #16 → #20 → #21 → #24 → #28 → #36
                    #11 → #12 ↗       #22,#23 ↗
              #15 → #17 → #18 → #19 ↗
#29 → skills #30-33
#34,#35 after #12,#22,#7
```

**Minimum viable demo (fastest path):** #1–#3, #4–#7, #11–#13, #15–#17 (single coder), #20–#24, #9, #36, #40 — skip parallel coders (#18), MCP (#34–35), RAG (#14), Mem0 (#41).

---

## Suggested GitHub milestone mapping

| Milestone | Issues |
|-----------|--------|
| M0 Bootstrap | 1–3 |
| M1 Workspace | 4–6 |
| M2 GitHub | 7–10 |
| M3 Context | 11–14 |
| M4 Agents | 15–19 |
| M5 Closed loop | 20–25 |
| M6 Review | 26–28 |
| M7 Skills & MCP | 29–35 |
| M8 Ship | 36–40 |

---

## Issue creation script (optional)

```bash
# After gh auth login and repo created:
while read -r title; do
  gh issue create --title "$title" --body-file "docs/issue-bodies/${title}.md" --label "enhancement"
done < docs/issue-titles.txt
```

You can split bodies from this file per issue when bulk-creating.
