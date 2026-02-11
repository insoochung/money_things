# Money Moves — Execution Engine Specification

The autonomous execution layer of a two-module investment system. Receives investment theses from money_thoughts, generates signals, routes them through Telegram for human approval, executes via Schwab API, tracks outcomes, and feeds results back.

This spec covers **design decisions and concepts**. For code-level details (schemas, API fields, exact formulas), read the code — it's the source of truth.

---

## System Overview

```
money_thoughts (conversational thesis development)
        │
        │  pushes thesis + ticker universe + criteria
        ▼
┌─────────────────────────────────────────────────────┐
│                   MONEY MOVES                       │
│                                                     │
│  Thesis Engine ──► Signal Engine ──► Approval Gate  │
│       │                 │                  │        │
│  Discovery    Confidence Scoring    Telegram Bot    │
│  (expand      (principles,          (approve /      │
│   ticker      domain weight,         reject /       │
│   universe)   source accuracy)       ignore)        │
│                                        │            │
│                              ┌─────────┼────────┐   │
│                              │         │        │   │
│                           Approve   Reject   Ignore │
│                              │         │     (24h)  │
│                              ▼         ▼        ▼   │
│                           Execute   what_if   what_if│
│                           (Schwab)  (tracked) (sep.) │
│                              │                      │
│                              ▼                      │
│                     Results + Reasoning             │
│                              │                      │
└──────────────────────────────│──────────────────────┘
                               │
                               ▼
                        money_thoughts
                   (outcome feedback loop)
```

---

## Key Design Decisions

### Thesis State Machine

Theses are not static — they evolve as evidence accumulates. The state machine drives signal generation: invalidated theses trigger SELL signals, strengthening theses may trigger BUY signals.

```
         ┌──────────────┐
         │    active     │ ◄── initial state on ingest
         └──────┬───────┘
                │
    ┌───────────┼───────────┐
    ▼           ▼           ▼
strengthening confirmed  weakening
    │           │           │
    └───────────┤           ▼
                │      invalidated
                │           │
                └───────────┘
                      │
                   archived
```

- **active** — default state on creation
- **strengthening** — supporting evidence accumulating
- **confirmed** — thesis validated by market data / earnings / news
- **weakening** — contradicting evidence appearing
- **invalidated** — failure criteria met, triggers SELL signals for linked positions
- **archived** — terminal state, no longer evaluated

### Signal Generation: Gate-Based, Not Scored

Thesis conviction (set by LLM reasoning via `/think` sessions) IS the confidence score. No weighted factor scoring. Deterministic gates guard entry:

```
BUY gates (all must pass — SELLs bypass):
1. Conviction ≥ 70%
2. ≥ 2 /think sessions (thesis_versions as proxy)
3. Thesis age ≥ 7 days
4. No imminent earnings (within 5 days)
5. Not in trading window blackout
6. Risk manager pre-trade check passes

Confidence = thesis.conviction (0.0–1.0)
Position size = base (2%) × confidence × 2, capped at max_position_pct
Signal dedup: one pending signal per ticker, updates existing on re-scan
```

After gates pass, `score_confidence()` applies adjustments: thesis strength multiplier, principle weights, domain expertise boost, source accuracy.

### Signal Lifecycle

```
Signal generated
       │
   [pending]  ──► Telegram notification sent to user
       │
       ├── User taps "Approve"  ──► [approved] ──► Execute via broker ──► [executed]
       │
       ├── User taps "Reject"   ──► [rejected] ──► Record to what_if
       │
       ├── 24h passes, no response ──► [ignored] ──► Record to what_if
       │
       └── Kill switch / risk limit ──► [cancelled]
```

### Reject vs Ignore: A Key Distinction

Rejections = active engagement ("I looked at this and disagree"). Ignores = passive disengagement ("I didn't engage"). Tracked separately in what_if to learn different things:

- **Reject accuracy**: Are the user's active disagreements correct?
- **Ignore cost**: How much alpha is lost to inattention?
- **Engagement quality**: Does engagement correlate with better decisions?

### Risk Management

Every signal passes 8 pre-trade checks before execution:

1. Kill switch (emergency halt)
2. Position size (max single position % of NAV)
3. Sector concentration (max sector %)
4. Gross exposure (max long + short)
5. Net exposure (within allowed band)
6. Trading window (META blackout enforcement)
7. Drawdown limit (halt if max DD breached)
8. Daily loss limit (halt new longs if exceeded)

All limits are configurable via the `risk_limits` table.

### Principles Engine

Self-learning investment rules. Applied during confidence scoring, validated by trade outcomes at 30/60/90 day marks. Principles that consistently lead to losses are auto-deactivated.

### What-If Tracking

Every rejected and ignored signal gets a what_if record tracking `price_at_pass`. Updated daily with current price and hypothetical P/L. Enables counterfactual analysis without risking real capital.

---

## Integration with money_thoughts

### Inbound (thoughts → moves)

Theses pushed via `POST /api/fund/theses` with: title, thesis_text, strategy, symbols, universe_keywords, validation/failure criteria, horizon, conviction.

### Outbound (moves → thoughts)

Results pushed back: thesis status, signals generated/approved/rejected, trades executed, total P&L, principle learnings, what-if summary, and LLM-generated reasoning summary. This enables money_thoughts to update conviction based on real outcomes.

---

## Remaining Gaps

Minor, non-blocking for mock mode:

1. **Sector concentration check** — always passes. `engine/discovery.py:SECTOR_MAP` has sector data that could be wired in.
2. **Cache TTLs** — hardcoded module constants in `engine/pricing.py`, could move to Settings.
3. **Swallowed exceptions** — `engine/pricing.py` DB cache writes use `except Exception: pass`; should log.
