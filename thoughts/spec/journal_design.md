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
| Signal generator | Gate-based, thesis conviction = confidence | **Simplified.** Was 6-factor weighted scoring, now gate checks only. LLM reasoning sets conviction via /think. |
| Thoughts module | Engine, bridge, commands, feedback, trigger monitor | **Fully wired.** /think spawns sub-agents, output parsed, approve/reject flow works. |
| Dashboard | 15+ sections, inline editing | **Complete.** Thesis and principle editing, watchlist triggers, 2yr price history. |
| Discovery engine | Finds tickers per thesis | Standalone, not wired to watchlist auto-creation. |
| News scanner | Scores articles | Standalone, not wired to signal generator (by design — gates only). |

### Status (2026-02-11): All P0-P3 complete.

All weak links resolved:
- ✅ /think spawns sub-agents, parses output, applies changes with approve/reject buttons
- ✅ Signal generator: gate-based (conviction ≥70%, ≥2 sessions, 7d cooldown, earnings, windows)
- ✅ Dashboard: inline editing for theses and principles, watchlist trigger management
- ✅ Outcome tracker: `engine/outcome_tracker.py` scores theses vs actual returns
- ✅ Watchlist triggers: full CRUD API + dashboard section

---

## 8. Remaining TODO

- [ ] First real `/think` e2e test with live sub-agent
- [ ] LLM reasoning for signal recommendations (gates done, reasoning not built)
- [ ] Import research files from original journal (META.md, QCOM.md, etc.) — deferred
- [ ] Schwab API activation — waiting on Schwab

---

## 9. What's NOT Changing
- moves module core code (engine, signals, trades)
- thoughts DB schema (existing tables are fine)
- Tax engine (keep, wire when Roth exists)
- Congress scoring (keep, runs independently from signals)
