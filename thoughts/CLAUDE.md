# money_thoughts

Conversational thesis development, research, review, and learning engine. The "thinking" half of the money system.

## Philosophy

- **Thesis-first** — Macro beliefs drive everything. Tickers come from theses, not the other way around.
- **Human in the loop** — AI assists, human decides. No autonomous trading here.
- **Learning machine** — Outcomes from money_moves feed back into principles and thesis refinement.

## Quick Start

| Command | Purpose |
|---------|---------|
| `/thesis "belief"` | Develop an investment thesis conversationally |
| `/discover T001` | Find tickers aligned with thesis |
| `/research AAPL` | Deep-dive research on a candidate |
| `/idea AAPL buy` | Create BUY/SELL recommendation |
| `/push T001` | Send validated thesis to money_moves |
| `/pulse` | Portfolio health scan (stores prices) |
| `/review` | Outcome review + learning |
| `/thought "..."` | Capture floating thoughts |
| `/synthesize` | Thesis-level portfolio view |
| `/remember` | Access persistent memory |
| `/refresh` | Update prices in research files |
| `/raise-cash 10000` | Tax-optimized sell recommendations |
| `/portfolio` | View/edit holdings |
| `/act 001` | Record executed trade |
| `/pass 001` | Record passed idea |
| `/context` | Improve system from friction |

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

```bash
source .venv/bin/activate && python3 -c "..."
```

## Evolved From

`~/workspace/money_journal/` — The original journal system. Preserved for historical logs.
