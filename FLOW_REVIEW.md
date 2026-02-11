# Full System Flow Review — 2026-02-11

## System Overview

Two modules, zero lint warnings:
- **money_moves** (moves/): FastAPI dashboard, trading engine, Schwab broker adapter
- **money_thoughts** (thoughts/): Thesis development, journal, research sub-agents

Live at **https://munnythoughts.com** with Google OAuth.

---

## Flow Trace: Idea → Trade

### 1. `/think <idea>` (thoughts/commands.py → context_builder.py → spawner.py)
✅ Looks up existing thesis by ID/name, builds context packet (positions, signals, past sessions, notes), computes slow-to-act gates, produces task string for sub-agent.

### 2. Sub-agent research (spawner.py → AGENT_PROMPT.md)
✅ 3-role approach (Researcher → Analyst → Critic). Returns structured JSON with research_summary, thesis_update, ticker_recommendations, conviction_change.

### 3. Thesis update (engine.py → moves DB)
✅ ThoughtsEngine reads/writes theses to moves DB. 7 theses seeded from original journal.

### 4. Signal generation (moves/engine/signal_generator.py)
✅ **Gate-based** — thesis conviction IS the confidence score (set by LLM via /think). Deterministic gates: ≥70% conviction, ≥2 /think sessions, ≥7 day thesis age, earnings block, trading window blackout. No weighted factor scoring.

### 5. Signal approval (moves/engine/approval.py → Telegram)
✅ Signals go to Telegram with approve/reject inline buttons.

### 6. Trade execution (moves/engine/broker/)
⏸️ Schwab API not activated. Mock broker functional.

### 7. Dashboard (moves/dashboard/)
✅ Thesis cards (expandable, editable), watchlist triggers, signal queue, what-if analysis, performance charts (2yr history, 1Y default), congress trades, macro indicators, principles (editable).

---

## Architecture Health

| Module | Lint | Status |
|--------|-------|------|--------|
| moves/ | ✅ | A |
| thoughts/ | ✅ | A |

## Remaining Items

### P1 — Important
1. **First real `/think` e2e test** — Full loop untested with live sub-agent
2. **LLM reasoning for signals** — Sub-agent that reads research history and produces signal recommendations (gate checks done, reasoning piece not built)
3. **Dashboard auth testing** — Verify full flow after Google login

### P2 — Nice to Have
4. **Mobile dashboard polish** — Some horizontal scroll on phones
5. **Import journal research data** — Deferred by user

### P3 — Deferred
6. **Schwab API activation** — Waiting on Schwab
7. **Multi-user support** — Spec exists at `moves/spec/multi_user.md`
8. **Portfolio rebalancing** — Not needed until 5-10+ positions
