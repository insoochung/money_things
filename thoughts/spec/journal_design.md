# money_thoughts — Journal Engine Design

## Revision History
- v0.1: Initial draft
- v0.2: Self-review — fixed announce callback gap, added human gate, clarified spawner limitations, added portfolio-level review
- v0.3: Signal generator rethink from original journal insights
- v0.4: Thesis-first philosophy — tickers are outputs of theses, not inputs

---

## 1. Success Criteria

### Must Have
- [ ] Each thesis gets an isolated Claude session with scoped context (no cross-thesis contamination)
- [ ] Trade outcomes from moves automatically feed back as journal learnings
- [ ] Research sessions produce structured output that updates moves DB (conviction, status)
- [ ] Quick thoughts route to the right thesis and surface during relevant research sessions
- [ ] The full loop works: think → research → signal → trade → outcome → learning → better thinking

### Should Have
- [ ] Minimal token burn — don't load irrelevant context into research sessions
- [ ] Human can override/correct any automated thesis update before it hits moves
- [ ] Journal entries are searchable and browsable (Telegram + dashboard)
- [ ] System learns from both wins AND losses (asymmetric learning is a trap)

### Must Not
- [ ] Cross-contaminate thesis contexts (cybersecurity session must not see biotech research)
- [ ] Auto-update moves DB without the research agent explaining why (no black box updates)
- [ ] Lose thoughts — every captured thought must be retrievable and linked
- [ ] Create infinite token loops (research spawns research spawns research)

---

## 2. Core Problems to Solve

### P0: Thesis-First — Tickers Are Outputs, Not Inputs
**Problem:** The original journal went ticker→thesis. The new system inverts this to thesis→tickers. But the current implementation still treats thesis symbols as static — you set them when creating the thesis and they don't evolve.

**Solution:** The research sub-agent's primary job is thesis development, which INCLUDES discovering and evolving the ticker universe:

```
/think "AI will eat cybersecurity"
    │
    ├─► Creates draft thesis (no tickers yet)
    ├─► Research agent investigates the macro belief
    ├─► Agent discovers: CRWD, PANW, ZS, FTNT fit the thesis
    ├─► Agent sets entry criteria for each
    ├─► Thesis updated with symbols + watchlist triggers
    │
    ├─► 3 months later, /think "AI cybersecurity" again
    ├─► Agent finds: ZS execution is weak, remove from thesis
    ├─► Agent finds: S (SentinelOne) now fits better
    ├─► Thesis symbols updated: CRWD, PANW, FTNT, S
    └─► Watchlist triggers updated accordingly
```

**Key principle:** The thesis OWNS its tickers. Adding/removing tickers from a thesis is a research output, not a manual action. The human approves (via Munny's gate), but the research agent proposes.

**Signal generator implication:** When scanning, it asks "what do this thesis's current symbols look like?" — and those symbols may have changed since last scan because research evolved them.

### P1: Context Isolation (Airlock)
**Problem:** Shared thoughts DB, no query scoping. A cybersecurity research session could read biotech notes.

**Solution:** Every sub-agent session gets a **context packet** — a pre-built markdown document containing ONLY:
- The specific thesis (title, text, criteria, conviction, status)
- Positions linked to that thesis
- Research notes for symbols in that thesis
- Thoughts tagged to that thesis
- Recent signals/trades for that thesis
- Outcome history for that thesis

Nothing else. The sub-agent's system prompt says "You have access to the context below. Do not query for other theses."

**Implementation:** `build_context_packet(thesis_id) -> str` — assembles the scoped markdown.

### P2: Outcome Feedback Loop
**Problem:** When trades execute and generate P&L, the journal never learns.

**Solution:** A scheduled job `job_outcome_review` that runs daily:
1. Query moves DB for trades closed in the last 7 days
2. For each, find the originating thesis
3. Write a "review" journal entry: what the thesis predicted, what actually happened, the P&L
4. If multiple trades show a pattern (thesis consistently right/wrong), flag for conviction update
5. Surface these reviews next time the thesis research session is resumed

### P3: Sub-Agent Spawning
**Problem:** Commands say "spawning..." but don't actually spawn.

**Solution:** Wire `/think` and `/research` to `sessions_spawn()` with:
- A thesis-scoped system prompt (AGENT_PROMPT.md + context packet)
- Task description: "Research thesis #N: {title}. Review current evidence, challenge assumptions, update conviction."
- The sub-agent's output is parsed for structured updates (JSON blocks) and journal entries

**Key constraint:** Sub-agents are fire-and-forget in OpenClaw. They run, produce output, and announce results. They don't maintain persistent conversation. So "resuming" a session means spawning a NEW sub-agent with the accumulated context (previous journals + research notes) — not actually continuing a thread.

This is actually fine for investment research. Each session is a fresh analysis with historical context, not a running conversation.

### P4: Thought Routing
**Problem:** `/thought` saves to a flat table, nothing reads it.

**Solution:**
1. On capture: auto-tag by matching symbols (regex for $TICKER or known ticker list) and thesis keywords
2. Store `linked_thesis_id` and `linked_symbol` 
3. When building a context packet for thesis #N, include all thoughts tagged to it
4. Weekly digest job: review unlinked thoughts, try to match to theses, surface orphans for manual tagging

---

## 3. Architecture

### Data Flow

```
USER INPUT                    AUTOMATED FEEDBACK
    │                               │
    ▼                               ▼
/think thesis ──────────► job_outcome_review
/research CRWD              (daily, reads trades)
/thought "..."                      │
/review META                        ▼
    │                     Review journal entries
    ▼                     written to thoughts DB
build_context_packet()              │
    │                               │
    ▼                               │
sessions_spawn()  ◄─────── context includes
    │                      past outcomes
    ▼
Sub-agent runs:
  - Reads context packet
  - Web search for news
  - Analyzes evidence
  - Produces structured output
    │
    ▼
Parse sub-agent output:
  - JSON thesis updates → bridge.push_thesis_update()
  - Research notes → engine.save_research()
  - Journal entry → engine.create_journal()
    │
    ▼
moves DB updated ──► signal_generator picks up
                     conviction changes
                         │
                         ▼
                    Signals → Telegram
                    approve/reject
                         │
                         ▼
                    Trade executes
                         │
                         ▼
                    P&L recorded ──► loops back to
                                     job_outcome_review
```

### Session Lifecycle

```
/think cybersecurity
    │
    ├─► Find thesis in moves DB
    ├─► build_context_packet(thesis_id=3)
    │     ├─ Thesis: "AI-driven cybersecurity..." 
    │     ├─ Positions: CRWD 0sh, PANW 0sh
    │     ├─ Research notes: [any previous]
    │     ├─ Thoughts: ["inference costs dropping...", ...]
    │     ├─ Signals: [pending/recent]
    │     ├─ Trade outcomes: [if any]
    │     └─ Previous reviews: [if any]
    │
    ├─► sessions_spawn(
    │     task="Research thesis: AI cybersecurity. 
    │           Context below. Analyze, challenge, 
    │           update conviction.",
    │     // context packet appended to task
    │   )
    │
    ├─► Sub-agent runs (3-8 min)
    │     ├─ Reads context
    │     ├─ Web searches current news
    │     ├─ Analyzes evidence for/against
    │     ├─ Outputs structured summary
    │     └─ Outputs JSON update block (if conviction changed)
    │
    └─► Announce callback:
          ├─ Parse output for JSON updates
          ├─ Apply thesis updates to moves DB
          ├─ Save research notes to thoughts DB
          └─ Save journal entry to thoughts DB
```

### Thought Lifecycle

```
/thought "NVIDIA losing inference share to custom ASIC"
    │
    ├─► Save to thought_log
    ├─► Auto-tag:
    │     symbol: NVDA
    │     thesis: #2 (AI infrastructure) — matched by keyword "inference"
    │
    ├─► Next /think #2 session:
    │     Context packet includes this thought
    │     Agent considers it in analysis
    │
    └─► Weekly digest:
          If untagged, surface for manual linking
```

---

## 4. Components to Build/Fix

### 4.1 context_builder.py (NEW)
```
build_context_packet(thesis_id) -> str
build_symbol_context(symbol) -> str
build_portfolio_overview() -> str
```
Pure functions that query both DBs and assemble scoped markdown.

### 4.2 spawner.py (NEW)
```
spawn_think_session(thesis_id) -> str  # returns session key
spawn_research_session(symbol, thesis_id?) -> str
parse_agent_output(output: str) -> AgentOutput  # extract JSON blocks + narrative
apply_agent_output(output: AgentOutput)  # write to DBs
```
Wires to OpenClaw sessions_spawn. Handles the announce callback parsing.

### 4.3 feedback.py (NEW)  
```
job_outcome_review()  # scheduled daily
generate_trade_review(trade) -> str  # single trade review
generate_thesis_review(thesis_id) -> str  # thesis-level review
detect_patterns(thesis_id) -> list[str]  # recurring wins/losses
```

### 4.4 thought_router.py (NEW)
```
auto_tag_thought(text) -> tuple[int|None, str|None]  # thesis_id, symbol
get_thoughts_for_thesis(thesis_id) -> list[dict]
get_orphan_thoughts() -> list[dict]
weekly_thought_digest() -> str
```

### 4.5 Update commands.py
- Wire /think to spawner.spawn_think_session()
- Wire /research to spawner.spawn_research_session()  
- Wire /thought to thought_router.auto_tag_thought() before saving
- Add /outcomes command for manual outcome review

### 4.6 Update engine.py
- Add moves_db path auto-detection (mock vs real)
- Add query methods scoped by thesis_id

### 4.7 Jobs (in moves scheduler)
- job_outcome_review: daily, feeds trade results back to thoughts
- job_thought_digest: weekly, reviews orphan thoughts

---

---

## 5. Self-Review: Issues Found in v0.1

### Issue 1: Announce callback is not interceptable
**Problem:** `sessions_spawn` announces results back to the *requester chat* (Telegram). The main agent (Munny) sees the announcement as a message, but there's no structured callback hook to parse JSON and apply DB updates automatically.

**Fix:** The announce goes through Munny. Munny's system prompt + AGENTS.md should include instructions: "When you receive an announcement from a thoughts sub-agent, parse any `<!--THESIS_UPDATE-->` blocks and apply them." This keeps Munny as the human-facing gatekeeper — Munny can summarize the research AND apply updates, or ask the user first.

**Revised flow:**
```
Sub-agent finishes → Announce to Munny
  → Munny parses output
  → Munny summarizes to user via Telegram
  → If thesis update found:
    → Munny asks: "Research suggests updating cybersecurity conviction from 60% to 75%. Apply?"
    → User approves → Munny calls bridge.push_thesis_update()
```

This is **better** than auto-applying because:
- Human stays in the loop for conviction changes
- Munny can challenge the sub-agent's conclusions
- No silent DB mutations

### Issue 2: No portfolio-level review
**Problem:** The design is thesis-centric but misses portfolio-level insights. "You're 100% concentrated in two RSU positions" is a portfolio-level observation, not a thesis-level one.

**Fix:** Add `build_portfolio_overview()` to context_builder and a `/synthesize` command that does a portfolio-level review. This spawns a sub-agent with the FULL portfolio context (all positions, all theses, allocation, risk metrics) and asks for a holistic assessment.

Different from `/think` (thesis-scoped) — `/synthesize` is portfolio-scoped and explicitly allowed to see everything.

### Issue 3: Thought auto-tagging is brittle
**Problem:** Matching "$TICKER" patterns and thesis keywords will miss nuanced thoughts like "I think the market is overvaluing growth" which doesn't mention any ticker.

**Fix:** Two-pass tagging:
1. First pass: regex for $TICKER and exact thesis title/keyword matches (fast, cheap)
2. Second pass (weekly digest): batch untagged thoughts and use the main agent to classify them. This is where LLM-based tagging makes sense — not on every thought capture.

### Issue 4: Research session depth control
**Problem:** No guidance on how deep a research session should go. A sub-agent could burn 100K tokens on a single thesis or stop after a surface-level take.

**Fix:** Add depth parameter to `/think`:
- `/think cybersecurity` → default depth (5 min, ~20K tokens)
- `/think cybersecurity deep` → deep dive (10 min, ~50K tokens)
- `/think cybersecurity quick` → quick check (2 min, ~5K tokens)

Depth controls: how many web searches, how many counter-arguments, how detailed the output.

### Issue 5: Missing the "so what" — signal generation from research
**Problem:** Research updates conviction in moves DB, but doesn't explicitly suggest trades. The signal_generator will eventually pick up conviction changes, but there's a gap between "conviction went up" and "buy 50 shares of CRWD."

**Fix:** The research agent's output format should include an explicit **action recommendation** that Munny can relay:
```
## Recommendation
- Action: BUY CRWD
- Size: 2% of portfolio (~$3,600)
- Account: Roth IRA
- Urgency: Low (no catalyst imminent)
- Reasoning: ...
```

This doesn't auto-create signals — it surfaces as a Telegram message from Munny that the user can act on. The signal_generator remains the autonomous path.

---

## 6. Revised Architecture (v0.2)

### Announce Flow (corrected)

```
/think cybersecurity
    │
    ├─► build_context_packet(thesis_id=3)
    ├─► sessions_spawn(task=prompt+context)
    │
    ▼
Sub-agent runs autonomously
    │
    ▼
Announce lands in Munny's session
    │
    ├─► Munny parses structured output
    ├─► Munny saves journal + research to thoughts DB (automatic)
    ├─► Munny summarizes findings to user via Telegram
    │
    ├─► If thesis update recommended:
    │     Munny: "Research suggests cybersecurity conviction → 75%. Apply?"
    │     User: "yes" / "no" / "make it 80%"
    │     → bridge.push_thesis_update()
    │
    └─► If trade recommended:
          Munny: "Research suggests BUY CRWD, 2% position in Roth. Create signal?"
          User: "yes"
          → signal created in moves DB
```

### Human Gates

| Action | Auto or Gated? |
|--------|---------------|
| Save journal entry | Auto |
| Save research notes | Auto |
| Tag thoughts | Auto (first pass) |
| Update thesis conviction | **Human gate** (Munny asks) |
| Create trade signal | **Human gate** (Munny asks) |
| Update thesis status | **Human gate** (Munny asks) |

### Components (revised)

| Component | Purpose | New/Fix |
|-----------|---------|---------|
| context_builder.py | Build scoped context packets | NEW |
| spawner.py | Wire sessions_spawn, define output format | NEW |
| feedback.py | Outcome review loop, pattern detection | NEW |
| thought_router.py | Auto-tag thoughts, weekly digest | NEW |
| commands.py | Wire to real spawner | FIX |
| engine.py | Scoped queries, mock DB detection | FIX |
| AGENT_PROMPT.md | Structured output format, depth control | FIX |
| AGENTS.md (main) | Announce parsing instructions for Munny | FIX |

---

## 7. Output Format Spec

Research sub-agents MUST end with this structure:

```markdown
## Summary
[1-3 sentence take]

## Conviction
- Previous: 60%
- Updated: 75%
- Direction: ↑ strengthening

## Key Findings
- [bullet points]

## Risks
- [bullet points]

## Recommendation
- Action: BUY/SELL/HOLD/WATCH
- Symbol: CRWD
- Size: 2% of portfolio
- Account: Roth IRA
- Urgency: low/medium/high
- Reasoning: [1-2 sentences]

## Next Review
- Trigger: [what would change this thesis]
- Date: [when to revisit]

<!--THESIS_UPDATE-->
{"thesis_id": 3, "conviction": 0.75, "status": "strengthening", "reasoning": "..."}
<!--/THESIS_UPDATE-->
```

---

## 8. Open Decisions

1. **Model for research sessions:** Same as main (opus) for quality, or sonnet for cost?
   → Start with same model. Monitor token spend. Switch to sonnet if costs are high and quality is acceptable.

2. **Dashboard journal view:** Show journals on web dashboard?
   → v2. Telegram-only for now.

3. **Multi-thesis thoughts:** What if a thought is relevant to 2+ theses?
   → Tag to the most relevant one. Can duplicate manually.

---

---

## 9. Scenario Stress Test

### Scenario A: First-time use
1. User runs `/onboard` → profile saved
2. User runs `/think "AI will eat cybersecurity"` 
3. No thesis exists yet → **Problem!** `/think` requires an existing thesis in moves DB.
4. **Fix:** `/think` should be able to CREATE a thesis from a raw idea string. If no match found, ask: "No thesis found. Create one?" or auto-create a draft thesis in moves DB with status "draft" and spawn the research session to flesh it out.

### Scenario B: Capturing a thought during commute
1. User sends `/thought NVDA losing inference share`
2. Auto-tag: symbol=NVDA. Thesis match? Check thesis keywords/symbols.
3. If thesis #2 has NVDA in symbols → tagged. ✅
4. If no thesis has NVDA → orphaned thought. Tagged to symbol only.
5. Next `/think` session for a thesis with NVDA picks it up. ✅

### Scenario C: Trade outcome feedback
1. Signal: BUY CRWD at $380 (from cybersecurity thesis)
2. User approves, trade executes
3. 30 days later, CRWD is at $420 (+10.5%)
4. `job_outcome_review` runs, finds this trade
5. Writes review: "CRWD buy was profitable. Thesis prediction (cybersecurity spend increasing) aligns with CRWD's earnings beat."
6. Next `/think cybersecurity` session includes this outcome.
7. Agent sees pattern: "2/2 cybersecurity picks profitable" → may recommend increasing conviction. ✅

### Scenario D: Research disagrees with current position
1. User runs `/think cybersecurity`
2. Sub-agent finds negative evidence: "CRWD valuation stretched, insider selling"
3. Output: conviction 60% → 35%, status: weakening
4. Munny relays: "Research suggests lowering cybersecurity conviction to 35%. Apply?"
5. User: "yes"
6. Moves DB updated → signal_generator may generate SELL signals for CRWD
7. Full loop completed. ✅

### Scenario E: Portfolio-level review
1. User runs `/synthesize`
2. Context includes ALL positions (META 230sh, QCOM 129sh), all theses
3. Agent: "100% concentrated in two RSU positions. Both are tech. No diversification. Recommend: establish new theses in healthcare/energy."
4. Munny relays. User: "good point, create a healthcare thesis"
5. → leads to new `/think healthcare` flow. ✅

### Scenario F: Token burn control
1. User runs `/think cybersecurity` three times in an hour
2. First run: full research (20K tokens). ✅
3. Second run: cooldown check — only 20 min since last. Block with message: "Last research was 20 min ago. Wait 40 min or use `/think cybersecurity quick` for a brief check."
4. Third run: after cooldown. Runs normally but context includes the first run's journal. ✅

---

## 10. Final Design Decision: Thesis Creation from /think

The v0.1 design assumed theses already exist in moves DB. But the natural flow is:
1. User has a raw idea
2. `/think` develops it into a thesis
3. Thesis gets created in moves DB
4. Signal generator acts on it

**Updated `/think` flow:**
```
/think "raw idea or thesis name"
    │
    ├─► Search moves DB for matching thesis
    │
    ├─► IF FOUND: build context packet, spawn research session
    │
    └─► IF NOT FOUND: 
          ├─► Create draft thesis in moves DB
          │   (title=idea, status="draft", conviction=0.0)
          ├─► Build minimal context (portfolio overview + thought)
          └─► Spawn session with task: "Develop this raw idea into 
               an investment thesis. Research the sector, identify 
               relevant tickers, define validation/failure criteria."
          └─► Output creates/updates the thesis with full details
```

This closes the gap between "I have an idea" and "the system is acting on it."

---

---

## 10b. Signal Generator Rethink

### What the original journal did (~/workspace/obsidian/money_journal/)

The original system had a much richer pipeline with four specialized agents:
1. **Researcher** — facts only, no opinions. Fetches fundamentals, news, prediction markets
2. **Analyst** — forms structured theses with success/failure criteria, review triggers
3. **Critic** — devil's advocate. Challenges every assumption, rates risk
4. **Synthesizer** — portfolio-level view across all theses

And a **watchlist with triggers:**
- Entry triggers (price targets for buys)
- Exit triggers (stop losses, take profits)
- Deadline triggers (earnings dates, trading windows)
- Stale research flags (>30 days old → refresh)

And **calibration tracking:**
- Win rate by conviction level
- Timeframe accuracy (did you hold as long as you planned?)
- Conviction calibration (are "high conviction" picks actually better?)

### What our signal generator is missing

**Current signal generator inputs:** thesis conviction + price movement >3%/5% + congress trades

**What it should also consider:**

1. **Watchlist triggers** — the original journal had specific entry/exit prices. If META hits $600 (the "add more" trigger), that should generate a signal. Currently we only react to thesis conviction, not price levels.

2. **Earnings calendar** — don't generate BUY signals 2 days before earnings (binary event risk). Don't generate signals during META's trading blackout. The watchlist tracked upcoming deadlines.

3. **News events** — `news_scanner.py` exists but isn't wired to signal generation. High-impact news on a thesis symbol should boost/lower signal confidence.

4. **Critic pass** — before a signal goes live, run the critic lens. "Is the thesis assumption still valid? Has something changed?" This catches stale theses generating signals on autopilot.

5. **Calibration feedback** — if our high-conviction signals historically only win 40% of the time, we should lower confidence scores. The metrics.md structure from the original journal tracks this.

6. **Discovery pipeline** — the original journal had structured discovery sessions that found new tickers per thesis. Our discovery engine exists but doesn't feed into signal generation. A discovery session should create watchlist entries with triggers.

### Revised Signal Generator Design

```
INPUTS (expanded):
├── Thesis conviction + status (existing)
├── Price triggers from watchlist (NEW)
│   ├── Entry targets: "buy if CRWD drops to $350"
│   ├── Exit targets: "sell if META hits $850"  
│   └── Stop losses: "sell if QCOM below $115"
├── Earnings calendar (NEW)
│   └── Block signals within N days of earnings
├── Trading windows (existing, but not wired)
│   └── Block META signals during blackout
├── News events (existing code, needs wiring)
│   └── High-impact news → boost/lower confidence
├── Congress trades (existing)
│   └── Whale-tier buys → low-confidence signal
├── Calibration data (NEW)
│   └── Historical accuracy adjusts confidence
└── Critic check (NEW)
    └── Pre-flight validation of thesis assumptions

PROCESS:
1. For each active thesis, evaluate all symbols
2. Check watchlist triggers (price, date, event)
3. Check if any blocking conditions (earnings, blackout, cooldown)
4. Score confidence using multi-factor model:
   - Thesis conviction (30%)
   - Price trigger proximity (20%)
   - News sentiment (15%)
   - Congress trade alignment (10%)
   - Calibration adjustment (10%)
   - Critic score (15%)
5. Generate signal if confidence > threshold
6. Route to appropriate account (tax engine)
7. Send to Telegram for approval

OUTPUT:
Signal with rich reasoning:
"BUY CRWD @ $395 → Roth IRA
 Thesis: AI cybersecurity (conviction 75%, strengthening)
 Trigger: price within 5% of $380 entry target
 News: +2 positive articles this week
 Congress: Pelosi bought PANW (sector aligned)
 Critic: thesis assumptions still valid, no earnings for 45 days
 Calibration: cybersecurity thesis 2/2 profitable historically
 Confidence: 0.72"
```

### Watchlist Table Addition

```sql
CREATE TABLE IF NOT EXISTS watchlist_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    thesis_id INTEGER,
    trigger_type TEXT NOT NULL,  -- 'entry', 'exit', 'stop_loss', 'take_profit'
    trigger_price REAL,
    trigger_date TEXT,  -- for date-based triggers
    trigger_event TEXT,  -- for event-based triggers  
    active INTEGER DEFAULT 1,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Earnings Calendar

Use existing free APIs (or scrape) to maintain an earnings calendar. Block signal generation N days before/after earnings for affected symbols.

---

## 11. What's NOT Changing
- moves module core code structure (engine, signals, trades)
- Telegram bot command names  
- thoughts DB schema (tables are fine)
- Dashboard (no journal view in v1)

## 12. What IS Changing
- Signal generator gets richer input model (watchlist triggers, news, earnings, calibration, critic)
- Thoughts module gets real sub-agent spawning with multi-agent roles (researcher, analyst, critic)
- Watchlist triggers as a new DB table in moves
- Outcome feedback loop (daily job)
- Thought routing and tagging
