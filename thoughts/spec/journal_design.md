# money_thoughts — Journal Engine Design v1.0

## Revision History
- v0.1-v0.4: Design iteration (see git history)
- v1.0: Simplified to 3 commands, full-flow review, honest assessment

---

## 1. Philosophy

**Thesis-first.** Ideas birth tickers, not the other way around.
**Slow to act.** Fewer trades, higher conviction. An idea must survive multiple research sessions before generating any signal.
**Human in the loop.** No silent DB mutations. Munny asks before changing conviction or creating signals.

---

## 2. Three Commands

```
/think <idea>    The ONE command. Develops thesis, discovers tickers, 
                 challenges assumptions. If thesis exists, deepens it.
                 If new, creates draft and researches from scratch.

/note <text>     Capture a quick observation. Auto-tagged to relevant 
                 thesis/symbol. Surfaces in next /think session.

/journal         Read-only. See recent research sessions, notes, and 
                 thesis history.
```

That's it. No /research, /review, /synthesize, /onboard, /outcomes as separate commands.
- Portfolio review is just `/think portfolio` (special case)
- Onboarding is first-run setup, not a recurring command
- Outcomes are surfaced automatically in /think context packets

---

## 3. The Full Loop

```
You have an idea
    │
    ▼
/think "AI eating cybersecurity"
    │
    ├─ Creates draft thesis in moves DB (if new)
    ├─ Builds context packet (thesis + positions + notes + outcomes)
    ├─ Spawns sub-agent with 3 roles:
    │   1. Researcher: facts, fundamentals, news
    │   2. Analyst: thesis structure, tickers, entry criteria
    │   3. Critic: challenges, risks, what could go wrong
    │
    ▼
Sub-agent runs (3-8 min, isolated context)
    │
    ▼
Announce lands in Munny's session
    │
    ├─ Munny saves journal + research (auto)
    ├─ Munny summarizes to you via Telegram
    │
    ├─ If thesis update: "Conviction → 75%. Apply?" (you approve)
    ├─ If ticker change: "Add FTNT, drop ZS. Apply?" (you approve)
    └─ If trade rec: "BUY CRWD, 2% in Roth. Create signal?" (you approve)
    │
    ▼
Thesis updated in moves DB
    │
    ▼
Signal generator picks up conviction changes (next 30-min scan)
    │
    ├─ Checks: thesis conviction, watchlist triggers, news, 
    │   earnings calendar, trading windows, calibration
    ├─ Blocks if: earnings imminent, blackout period, cooldown
    │
    ▼
Signal generated → Telegram → You approve/reject
    │
    ▼
Trade executes → P&L tracked
    │
    ▼
Daily outcome review job writes results back to thoughts DB
    │
    ▼
Next /think session includes: "CRWD +10.5%, thesis was right"
    │
    ▼
Better research → Better conviction → Better signals
```

---

## 4. Slow-to-Act Gates

| Gate | Rule |
|------|------|
| New thesis | No signals until /think'd at least **2 separate sessions** |
| Conviction threshold | Must be ≥ 70% across both sessions |
| Critic pass | No unresolved high-risk challenges |
| Cooldown | Min **1 week** between thesis creation and first signal |
| Same-thesis /think | Min **1 hour** between sessions |
| Earnings block | No signals within **5 days** of earnings |
| Trading window | Respect blackout periods (META) |

---

## 5. Dashboard Inline Editing

Add edit capability to the dashboard for the three things that drive everything:

**Theses:**
- Click thesis card → expand with edit fields
- Edit: title, description, conviction (slider), status (dropdown), symbols (tag input)
- Add/archive thesis
- View linked signals, positions, research history

**Principles:**
- Click principle → inline edit
- Edit: text, category, weight (slider)
- Add new / mark validated / mark invalidated
- Show validation count and origin

**Watchlist Triggers:**
- Table with inline edit
- Add/edit: symbol, trigger type (entry/exit/stop/take-profit), price, notes
- Toggle active/inactive
- Show which thesis owns the trigger

All edits go through existing API endpoints (POST/PUT). No new backend needed.

---

## 6. Signal Generator — Revised Inputs

Current: thesis conviction + price >3% move + congress trades

Revised:

| Input | Weight | Source |
|-------|--------|--------|
| Thesis conviction | 30% | moves DB (from /think sessions) |
| Watchlist trigger hit | 20% | NEW table: watchlist_triggers |
| News sentiment | 15% | news_scanner (wire existing code) |
| Critic pre-flight | 15% | Last /think critic assessment |
| Calibration | 10% | Historical win rate for this thesis |
| Congress alignment | 10% | Existing politician scoring |

**Blocking conditions:** earnings calendar, trading windows, thesis age <1 week, conviction <70%, <2 /think sessions

---

## 7. What's Overengineered

Honest assessment of what we've built vs what we need:

| Component | Status | Verdict |
|-----------|--------|---------|
| Congress politician scoring | 37 tests, full tier system | **Overengineered.** We have 2 positions (META, QCOM RSUs). Congress trades are a nice-to-have signal, not a driver. The whale/notable/average/noise tiers are cool but won't matter until we're actively trading 10+ symbols. Keep but deprioritize. |
| Tax engine + routing comparison | 34 tests, 10-scenario comparison | **Right-sized for later, premature now.** You have 2 accounts, both taxable brokerage (no Roth yet). Tax lot tracking is useful for META/QCOM sells, but the routing engine has nothing to route TO. Wire it when Roth IRA is set up. |
| Multi-user spec | Full spec written | **Correct decision to defer.** One user, build for one. |
| Signal generator | Runs every 30min, thesis-only | **Under-engineered.** Missing watchlist triggers, news, earnings calendar, calibration. The most important component is the thinnest. |
| Thoughts module (current) | Engine, bridge, commands — all stubs | **Right idea, wrong execution.** Commands don't actually spawn anything. Bridge reads/writes but nobody calls it. Needs complete rewiring. |
| Dashboard | 15+ sections | **Over-scoped for read-only.** 15 sections but can't edit the 3 things that matter (theses, principles, watchlist). Add edit, cut sections nobody uses. |
| Discovery engine | Finds tickers per thesis | **Good but disconnected.** Does discovery but doesn't create watchlist entries or feed signals. |
| News scanner | Scores articles | **Good but disconnected.** Scores news but doesn't feed signal generator. |

### Weak Links (priority order)

1. **Thoughts → Moves connection is broken.** /think doesn't spawn anything. The entire thesis development loop is a stub.
2. **Signal generator is too thin.** Only reads conviction. Doesn't check watchlist, news, earnings, calibration.
3. **Dashboard can't edit.** You can see everything but change nothing.
4. **No outcome feedback.** Trades happen but learnings never flow back.
5. **Watchlist doesn't exist as a DB table.** The original journal had it, we don't.

---

## 8. TODO (Priority Order)

### P0: Make /think actually work
- [ ] `context_builder.py` — build thesis-scoped context packets
- [ ] `spawner.py` — wire to `sessions_spawn()`, define output format
- [ ] Update `commands.py` — 3 commands only (/think, /note, /journal)
- [ ] Update `AGENT_PROMPT.md` — researcher+analyst+critic in one session
- [ ] Update Munny's AGENTS.md — announce parsing instructions
- [ ] Slow-to-act gates (2 sessions, 1 week cooldown, conviction threshold)
- [ ] Import principles + watchlist from original journal

### P1: Enrich signal generator
- [ ] `watchlist_triggers` table + migration
- [ ] Wire news_scanner into signal confidence
- [ ] Earnings calendar (free API or scrape)
- [ ] Wire trading windows check (table exists, not checked)
- [ ] Calibration tracking (win rate per thesis)
- [ ] Multi-factor confidence scoring

### P2: Dashboard editing
- [ ] Thesis inline edit (conviction slider, status dropdown, symbol tags)
- [ ] Principles inline edit (text, weight slider, validated toggle)
- [ ] Watchlist trigger management (add/edit/toggle)
- [ ] Add /note equivalent (quick thought capture from dashboard)

### P3: Outcome feedback loop
- [ ] `feedback.py` — daily job reads closed trades, writes review journals
- [ ] Pattern detection (thesis consistently right/wrong)
- [ ] Calibration data updates

### P4: Cleanup
- [ ] Remove unused Telegram commands (/research, /review, /synthesize, /onboard, /outcomes)
- [ ] Prune dashboard sections nobody uses
- [ ] Import research files from original journal (META.md, QCOM.md, etc.)

---

## 9. What's NOT Changing
- moves module core code (engine, signals, trades)
- thoughts DB schema (existing tables are fine)
- Tax engine (keep, wire when Roth exists)
- Congress scoring (keep, deprioritize)
