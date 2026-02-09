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
3. ~~**Dashboard watchlist triggers not visible without auth bypass**~~: ✅ **DONE (2026-02-09)** — All dashboard `fetch()` calls centralized into `api()` (reads) and `apiWrite()` (mutations) helpers. Both redirect to `/auth/login` on 401/403. Commit `e45f006`.
4. ~~**Earnings calendar is a static JSON file**~~: ✅ **DONE (2026-02-09)** — `earnings_calendar.py` now falls back to yfinance API for symbols not in static JSON. 24h cache. 14 tests. Commit `218ddbd`.
5. ~~**Test suite hangs when run all-together**~~: ✅ **DONE (2026-02-09)** — Root cause: import conflict (both modules have `engine/` package). Created `run_tests.sh` that runs each module's tests in its own venv. 622 tests pass (451 moves + 171 thoughts). Commit `8443847`.

### P2 — Nice to Have
6. ~~**No `/think` result parsing**~~: ✅ **DONE (2026-02-09)** — `commands.cmd_think_result()` parses sub-agent JSON, auto-applies research artifacts (journal entries, notes, ticker recs), formats Telegram summary, and returns inline button specs for approve/reject of conviction and thesis changes. `cmd_think_approve()` / `cmd_think_reject()` handle callbacks. 12 new tests. Commit `08771dd`.
7. **Import more journal data**: Research files (META.md, QCOM.md etc.) have rich content that could be imported as research notes.
8. ~~**Congress scoring not wired into signal generator**~~: ✅ **DONE (2026-02-09)** — `SignalGenerator` now uses `PoliticianScorer.score_trade()` for congress alignment factor. Trades weighted by size, stock-vs-ETF, committee relevance, politician tier. Enriched reasoning shows politician details in signal output. 5 new tests. Commit `7dff5ec`.
9. ~~**No daily briefing command**~~: ✅ **DONE (2026-02-09)** — `cmd_brief()` fetches live prices for all thesis symbols + watchlist items, shows trigger proximity with alerts (⚠️ <5%, 👀 <10%), upcoming earnings within 7 days, recent notes, pending signals. 10 tests. Commit `d81e41a`.
10. ~~**No proactive trigger monitoring**~~: ✅ **DONE (2026-02-09)** — `trigger_monitor.py` checks live prices against all active watchlist triggers. Three alert levels: critical (<3%), warning (<7%), watch (<15%). `format_alerts()` produces Telegram notifications. Can be called from heartbeats. 11 tests. Commit `2581bf0`.

### P3 — Deferred
11. **Schwab API activation**: Waiting on Schwab.
12. **Multi-user support**: Spec exists, deferred.
13. **Portfolio rebalancing**: No automated rebalancing engine. Thesis death drives exits, but concentration limits not enforced.

---

## Architecture Health

| Module | Lines | Tests | Lint | Grade |
|--------|-------|-------|------|-------|
| moves/ | ~22K | 491 | ✅ | A |
| thoughts/ | ~12K | 192 | ✅ | A |
| **Total** | **34K** | **683** | **✅** | **A** |

**Thoughts grade rationale (A):** Core engine, bridge, commands, feedback loop, daily briefing, and trigger monitoring all work. Sub-agent output is parsed, auto-applied, and presented with approve/reject buttons. The full /think → research → parse → approve → DB update pipeline is functional. `/brief` provides daily overview with live prices, trigger proximity, and earnings.

---

## Recommendation

**Next session priority**: Import journal research data as notes (P2 #7) — deferred per user. System is ready for real use: all commands functional, dashboard auth-aware, 683 tests passing. Daily workflow: `/brief` for morning check-ins, `trigger_monitor` for proactive alerts, `/think` for research. Core loop fully operational.
