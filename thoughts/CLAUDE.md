# money_thoughts

Conversational thesis development, research, review, and learning engine. The "thinking" half of the money system.

## Philosophy

- **Thesis-first** — Macro beliefs drive everything. Tickers come from theses, not the other way around.
- **Human in the loop** — AI assists, human decides. No autonomous trading here.
- **Learning machine** — Outcomes from money_moves feed back into principles and thesis refinement.

## Quick Start

| Command | Purpose |
|---------|---------|
| `/think "idea"` | Research an idea with AI sub-agent |
| `/think result <json>` | Apply research output (auto-called after /think) |
| `/note <text>` | Add a note to the journal |
| `/journal` | Display recent journal entries |
| `/brief` | Daily briefing with prices, triggers, earnings |
| `/trade <details>` | Record executed trade |

## Integration Commands
These are available when called from the money_moves Telegram bot:
- Thesis research and development 
- Context building from portfolio state
- Sub-agent spawning for deep analysis
- Research output parsing and database updates

## Thesis Lifecycle

```
Conversation → /thesis → theses/active/
  → /discover (find tickers) → /research (validate)
  → /idea (trade rec) → /push (send to money_moves)
  → money_moves executes → results port back
  → /review → principles updated → next thesis informed
```

## Interface

- **Primary:** OpenClaw via Telegram (always-on, scheduled)
- **Secondary:** Claude Code CLI (terminal sessions)

## Data

- **Markdown** — Theses, research, ideas, reviews, thoughts (human reasoning, git-versioned)
- **SQLite** — Prices, trades, portfolio snapshots (`data/journal.db`)

## Integration

- **Sends to money_moves:** Validated theses + ticker universe + criteria via `/push`
- **Receives from money_moves:** Trade results, P&L, what-ifs, LLM reasoning summaries

## Full Specification

See `spec/money_thoughts.md` for complete details including folder structure, command specs, data model, memory system, and integration formats.

## Python Environment

This module integrates with money_moves via the bridge.py interface.
Commands are triggered through the moves Telegram bot handler.

```bash
# Testing individual functions
cd ~/workspace/money/thoughts
python3 -c "from commands import cmd_brief; print(cmd_brief())"

# Testing with specific engine
python3 -c "from engine import ThoughtsEngine; e = ThoughtsEngine(); print(e.db.summary())"
```

## Evolved From

`~/workspace/money_journal/` — The original journal system. Preserved for historical logs.
