# Go OSS Agentic Contributor

An agentic AI platform that takes a GitHub issue from an approved Go OSS project, plans and implements a fix, validates it in a closed loop, reviews the change, and produces a PR (or local branch + PR summary).

**Approved targets:** [gin-gonic/gin](https://github.com/gin-gonic/gin), [spf13/cobra](https://github.com/spf13/cobra), [go-playground/validator](https://github.com/go-playground/validator), [golangci/golangci-lint](https://github.com/golangci/golangci-lint)

## Quick start (after implementation)

```bash
# Prerequisites: Python 3.11+, Go 1.22+, gh CLI, OPENAI_API_KEY (or ANTHROPIC_API_KEY)
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Edit .env with your LLM provider and GitHub token

# Run on an issue (dry-run: no PR)
go-agent run --repo gin-gonic/gin --issue 1234 --dry-run

# Full run with PR
go-agent run --repo spf13/cobra --issue 567 --create-pr
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for system design and [docs/GITHUB_ISSUES.md](docs/GITHUB_ISSUES.md) for the step-by-step implementation backlog.

## Stack (recommended)

| Concern | Choice | Why |
|--------|--------|-----|
| Orchestration | **LangGraph** | Native **cycles** (code → test → fix) and explicit state; better fit than CrewAI for closed-loop agents |
| Multi-agent “team” | LangGraph nodes + parallel sub-runs | Fast file-scoped coder workers; optional CrewAI layer only if you prefer its task API |
| LLM routing | **LiteLLM** | One interface for OpenAI / Anthropic / local models |
| Repo memory (run) | LangGraph checkpointer + `artifacts/` | Reproducible runs for reviewers |
| Long-term memory | **Repo index + skills** (not Mem0 first) | Assignment is per-repo/issue; Mem0 adds ops cost with little gain until you run many issues |
| Optional persistence | Mem0 or **SQLite + embeddings** | Use when you want cross-issue learning on the same repo |
| Tools / IDE integration | **MCP server** (`mcp/`) | Search, read, edit, `go test`, `gh` — same tools for agents and Cursor |
| GitHub | **PyGithub** + `gh` subprocess | Issue fetch, branch, PR create |

## Repository layout (target)

```
go-agent/
  cli.py                 # Typer CLI entry
  config.py
  orchestrator/          # LangGraph workflow
  agents/                # planner, coder, reviewer prompts
  tools/                 # github, git, search, subprocess test
  memory/                # index, run store
  skills/                # gin, cobra, validator, golangci-lint
mcp/
  server.py              # MCP tools for repo + validation
docs/
  ARCHITECTURE.md
  GITHUB_ISSUES.md
tests/
```

## License

MIT (assignment submission)
