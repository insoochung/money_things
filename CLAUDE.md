# Money

Two-module investment system: think, then move.

## Modules

| Module | Path | Role | Interface |
|--------|------|------|-----------|
| **money_thoughts** | `thoughts/` | Conversational thesis development, research, review, learning | Claude Code CLI + OpenClaw/Telegram |
| **money_moves** | `moves/` | Autonomous execution engine, web dashboard, Schwab API | Web dashboard + Telegram signals |

## Architecture

```
User (Telegram / CLI)
     │
     ▼
money_thoughts ──── thesis + tickers ────► money_moves
  (thinking)        ◄── results + reasoning ──  (executing)
     │                                            │
     │                                            │
  Principles ◄──── learnings ◄──── outcomes ◄─────┘
```

### Flow

1. **Think** — User develops investment theses conversationally in money_thoughts
2. **Discover** — money_thoughts finds tickers aligned with thesis, validates via research
3. **Push** — Validated thesis + ticker universe pushed to money_moves
4. **Execute** — money_moves generates gate-checked signals (conviction ≥70%, ≥2 /think sessions, 7d cooldown), sends to Telegram for approval
5. **Approve/Reject/Ignore** — User responds via Telegram (or 24h timeout = ignore)
6. **Trade** — Approved signals execute via Schwab API
7. **Learn** — Results + reasoning summaries port back to money_thoughts for review
8. **Evolve** — Principles updated, theses refined, next iteration informed

### Key Design Decisions

- **Thesis-first** — Everything flows from macro beliefs, not individual tickers
- **Human-in-the-loop** — AI generates signals, human approves via Telegram
- **Feedback loop** — Execution outcomes improve thesis quality over time
- **Mid/long term** — Minimal positions per thesis, not day trading
- **What-if tracking** — Rejected AND ignored signals tracked for learning
- **Tax-aware** — Lot-level optimization, holding period awareness, funding plan intelligence

## Glue Layer: OpenClaw

OpenClaw runs on a VPS, bridging both modules via Telegram:
- Receives signals from money_moves → sends to Telegram
- Routes approvals back to money_moves
- Runs scheduled /pulse, /review on money_thoughts
- Can be used for remote development of both codebases

See `moves/spec/clawdbot.md` for full setup guide.

## Module Docs

- `thoughts/CLAUDE.md` — money_thoughts entry point
- `moves/CLAUDE.md` — money_moves entry point
- `moves/spec/money_moves.md` — design decisions (high-level)
- `moves/spec/clawdbot.md` — OpenClaw setup guide

## Coding Standards

This is an **AI-maintained codebase**. All agents working on this repo must follow these rules.

### Code Style
- **Small, single-purpose functions** — Each function does one thing. Minimize parameters. Refactor instead of adding arguments.
- **Thorough docstrings** — Every module, class, and function gets detailed docstrings. Explain what, why, parameters, returns, side effects, and system context. More detail = better context for future LLM agents.
- **Type hints** — All function signatures typed. Use Pydantic models for data structures.
- **No unnecessary comments** — Don't comment obvious code. Docstrings carry the context, inline comments only where logic isn't self-evident.

### Testing
- **TDD** — Write tests before or alongside implementation. Every module gets a test file.
- **No trivial tests** — Don't test Python itself. No testing that constructors set fields or that 1+1=2. Test real behavior, edge cases, and integration points that could actually break.
- **Tests must pass** before moving on to the next task.

### Linting
- **ruff** — Shared config at repo root `pyproject.toml`. Run `ruff check` and `ruff format` before any task is considered done. Zero warnings policy.

### Process
- **Phase reviews** — Each implementation phase gets a review stage before the next phase begins.
- **Leave trails** — Docstrings, commit messages, and code structure should make the "why" obvious to the next agent.

## Source Repo

Evolved from `~/workspace/money_journal/` (preserved as-is for historical logs).
