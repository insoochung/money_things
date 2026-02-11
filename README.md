# Money System — Investment Thesis Engine

A two-module AI-driven investment system: **money_thoughts** (thesis development) + **money_moves** (autonomous execution).

## Quick Start

```bash
# Dashboard (live at https://munnythoughts.com)
cd moves/
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 run.py
# → http://localhost:8000

# Tests
cd moves/ && ./run_tests.sh
cd thoughts/ && python3 -m pytest
```

## Architecture

```
money_thoughts ──► Thesis Development (conversational AI research)
        │
        ▼
money_moves ───► Execution Engine (web dashboard, Schwab API, Telegram bot)
```

| Module | Purpose | Tech Stack |
|--------|---------|-------|------------|
| **moves/** | Portfolio dashboard, signal engine, execution | FastAPI, SQLite, schwab-py |
| **thoughts/** | AI research, thesis development, context building | Python, OpenClaw integration |

## Key Features

- **Thesis-First Investing** — Macro beliefs drive stock selection, not the other way around
- **AI Research Assistant** — `/think` command spawns research sub-agents via OpenClaw
- **Gate-Based Signals** — Thesis conviction = confidence; deterministic gates (conviction ≥70%, ≥2 /think sessions, 7-day cooldown, earnings block) guard entry
- **Live Dashboard** — Notion-inspired portfolio view with thesis cards, watchlist triggers, 2yr price history
- **Tax Optimization** — Lot-specific trade recommendations with tax impact analysis
- **Risk Management** — Exposure tracking, correlation matrices, kill switches
- **Audit Trail** — Every decision recorded with reasoning and performance tracking

## Status

**Production-ready.** Live at munnythoughts.com with Google OAuth. Full test suite via `./run_tests.sh`.

See `moves/CLAUDE.md` + `thoughts/CLAUDE.md` for module documentation and `moves/REVIEW.md` for quality status.

## Integration

Designed for **OpenClaw** (self-hosted AI assistant) via Telegram:
- Daily briefings (`/brief`)
- Research sessions (`/think`)
- Signal approvals (inline buttons)
- Proactive trigger proximity alerts (cron)
- Trade execution tracking

---

*Evolved from `money_journal/` — the original research system.*
