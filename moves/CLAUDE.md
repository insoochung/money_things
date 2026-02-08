# money_moves

Autonomous execution engine with web dashboard. The "doing" half of the money system.

## Philosophy

- **Thesis-driven** — Every position tied to a thesis from money_thoughts
- **Autonomous with guardrails** — AI generates signals, human approves via Telegram, engine executes
- **Full audit trail** — Every decision, signal, and execution recorded with reasoning

## Architecture

```
money_thoughts ──► POST /api/fund/theses ──► Thesis Engine
                                                 │
                                           Signal Engine
                                           (LLM + confidence scoring)
                                                 │
                                           Telegram Bot
                                           (approve / reject / ignore)
                                                 │
                                     ┌───────────┼───────────┐
                                  Approve     Reject      Ignore (24h)
                                     │           │            │
                                  Execute     what_if      what_if
                                  (Schwab)    (tracked)    (separate)
                                     │
                              Results → money_thoughts
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| API | FastAPI + WebSocket |
| Database | SQLite WAL (20+ tables) |
| Broker | schwab-py (Schwab official API) |
| Bot | python-telegram-bot |
| Frontend | Vanilla JS, Inter font, Notion-inspired |
| Scheduler | APScheduler |
| Testing | pytest (TDD) |

## Modes

| Mode | Database | Broker | Purpose |
|------|----------|--------|---------|
| Mock | `data/moves_mock.db` | Fake fills (yfinance) | Development and testing |
| Live | `data/moves_live.db` | Schwab API | Real money |

## Dashboard (Notion-Inspired)

```
Colors: #ffffff bg, #37352f text, #f7f7f5 hover, #e8e8e4 border
Font: Inter / IBM Plex Mono
Style: content-first, no chrome, warm, typography-driven
```

15-section layout: Header → Summary Cards → Macro Strip → Risk → Theses → Exposure → Correlation → Position Heatmap → Positions Table → Performance Chart → Drawdown → Trades → Congress Trades → Principles → Footer

## Key Features

- **Signal Engine** — LLM-scored confidence with thesis strength, principles, domain expertise, source accuracy
- **Telegram Bot** — Approve/reject inline buttons, 24h ignore timeout
- **What-If Tracking** — Rejected vs ignored distinction (engagement vs conviction)
- **Funding Plan** — Buy signals include which lot to sell + tax impact
- **META Window** — Blocks signals outside trading windows
- **Principles Engine** — Self-learning rules, validated by outcomes
- **Congress Trades** — Politician trading as sentiment signal
- **Domain Expertise** — Configurable profile (not hardcoded)

## Full Specification

- `spec/money_moves.md` — Complete spec (schema, APIs, dashboard, signal flow)
- `PLAN.md` — Phased implementation plan
- `spec/clawdbot.md` — OpenClaw/Telegram setup guide

## Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Development Rules

1. All prices from APIs, never LLM-estimated
2. All metrics computed in Python, never LLM-generated
3. Every signal must have a thesis_id
4. Every execution must have an audit_log entry
5. TDD: tests alongside implementation
6. Mock mode fully functional before live mode
