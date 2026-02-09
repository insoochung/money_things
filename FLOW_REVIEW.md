# Full System Flow Review — 2026-02-09

## System Overview

**33K lines of Python** across 113 files, **538 tests**, two modules:
- **money_moves** (moves/): FastAPI dashboard, trading engine, Schwab broker adapter
- **money_thoughts** (thoughts/): Thesis development, journal, research sub-agents

Live at **https://munnythoughts.com** with Google OAuth.

---

## Flow Trace: Idea → Trade

### 1. `/think <idea>` (thoughts/commands.py → context_builder.py → spawner.py)
✅ **Working.** Looks up existing thesis by ID/name, builds context packet (positions, signals, past sessions, notes), computes slow-to-act gates, produces task string for sub-agent.

### 2. Sub-agent research (spawner.py → AGENT_PROMPT.md)
✅ **Working.** 3-role approach (Researcher → Analyst → Critic). Returns structured JSON with research_summary, thesis_update, ticker_recommendations, conviction_change.

### 3. Thesis update (engine.py → moves DB)
✅ **Working.** ThoughtsEngine reads/writes theses to moves DB. 7 theses seeded from original journal.

### 4. Signal generation (moves/engine/signal_generator.py)
✅ **Working.** Multi-factor scoring: thesis conviction (30%), watchlist trigger (20%), news (15%), critic (15%), calibration (10%), congress (10%). Slow-to-act gates: ≥2 sessions, ≥70% conviction, 1 week cooldown, earnings block, trading window check.

### 5. Signal approval (moves/engine/approval.py → Telegram)
✅ **Working.** Signals go to Telegram for approval before execution.

### 6. Trade execution (moves/engine/broker/)
⏸️ **Blocked.** Schwab API not activated. Mock broker functional.

### 7. Dashboard (moves/dashboard/)
✅ **Working.** Thesis cards (expandable, now editable), watchlist triggers section, signal queue, what-if analysis, performance charts, congress trades, macro indicators.

---

## What Got Fixed Tonight

| Issue | Fix | Commit |
|-------|-----|--------|
| No journal engine | context_builder.py, spawner.py, 3-command structure | `8d026fd` |
| No watchlist triggers table | Migration 004 + full CRUD API + dashboard section | `2efb724` |
| Dashboard read-only | Inline edit for theses (title, description, conviction, status, symbols) | `2efb724` |
| Signal generator too thin | Multi-factor scoring (6 factors), earnings block, trading windows | `ef1f16f` |
| No thesis data | 7 theses + 11 triggers imported from original journal | `82476a7` |
| Conviction scale bug | Handle 0-100 vs 0-1 scale from DB | `9b4650a` |
| Symbol parsing bug | Handle comma-separated (not just JSON arrays) | `9b4650a` |
| Pre-existing test failures | Dashboard rebrand, OAuth redirect test | `82476a7` |

---

## Remaining Weak Links (Priority Order)

### P0 — Critical Path
1. ~~**Outcome feedback loop missing**~~: ✅ **DONE (2026-02-09)** — `engine/outcome_tracker.py` scores theses against actual returns. Calibration scoring (0-100), daily snapshots via `outcome_snapshots` table, REST API at `/api/fund/outcomes`, Telegram-formatted scorecards. 19 tests. Commit `d79a1be`.
2. ~~**Sub-agent output → DB pipeline**~~: ✅ **DONE (previously)** — `thoughts/feedback.py` parses sub-agent JSON, saves journal/notes, queues conviction changes for approval. Tests in `thoughts/tests/test_feedback.py`.

### P1 — Important
3. **Dashboard watchlist triggers not visible without auth bypass**: Can't verify visually. Need test or screenshot.
4. ~~**Earnings calendar is a static JSON file**~~: ✅ **DONE (2026-02-09)** — `earnings_calendar.py` now falls back to yfinance API for symbols not in static JSON. 24h cache. 14 tests. Commit `218ddbd`.
5. **Test suite hangs when run all-together**: Individual files pass (538 tests) but running `pytest tests/` hangs. Likely SQLite locking under concurrent access. Need `--forked` or DB isolation.

### P2 — Nice to Have
6. **No `/think` result parsing**: When the sub-agent finishes, Munny needs to parse the JSON output and present it to the user with options to accept/reject conviction changes.
7. **Import more journal data**: Research files (META.md, QCOM.md etc.) have rich content that could be imported as research notes.
8. **Congress scoring not wired into signal generator**: The `congress_scoring.py` module exists but the signal generator uses a basic `thesis_congress` table check, not the full scoring engine.

### P3 — Deferred
9. **Schwab API activation**: Waiting on Schwab.
10. **Multi-user support**: Spec exists, deferred.
11. **Portfolio rebalancing**: No automated rebalancing engine. Thesis death drives exits, but concentration limits not enforced.

---

## Architecture Health

| Module | Lines | Tests | Lint | Grade |
|--------|-------|-------|------|-------|
| moves/ | ~22K | 393 | ✅ | A- |
| thoughts/ | ~11K | 145 | ✅ | B+ |
| **Total** | **33K** | **538** | **✅** | **A-** |

**Thoughts grade rationale (B+):** Core engine works, bridge works, commands work. But the sub-agent output parsing pipeline is incomplete — the "last mile" between research output and thesis DB mutation is manual.

---

## Recommendation

**Next session priority**: Build the output parser (`thoughts/feedback.py`) that takes sub-agent JSON, presents it to user, and updates thesis on approval. This closes the loop and makes `/think` fully functional end-to-end.
