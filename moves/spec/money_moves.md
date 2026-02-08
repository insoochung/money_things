# Money Moves — Execution Engine Specification

The autonomous execution layer of a two-module investment system. Receives investment theses from money_thoughts, discovers tickers, generates LLM-scored buy/sell signals, routes them through Telegram for human approval, executes via Schwab API, tracks outcomes, and feeds results back to money_thoughts.

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

## Architecture

### Module Boundary

| Module | Responsibility | State |
|--------|---------------|-------|
| **money_thoughts** | Conversational thesis development, research, reasoning | Markdown files, conversation history |
| **money_moves** | Autonomous execution, portfolio management, tracking | SQLite database, web dashboard |

money_moves is **not** conversational. It is a running service with a web dashboard and Telegram bot interface. money_thoughts pushes theses to money_moves via its API; money_moves pushes results back.

### Directory Structure

```
money_moves/
├── CLAUDE.md                  # Project entry point
├── PLAN.md                    # Phased implementation plan
├── spec/
│   └── money_moves.md         # This file
├── engine/
│   ├── __init__.py
│   ├── core.py                # Orchestration, startup, shutdown
│   ├── thesis.py              # Thesis engine: ingest, validate, state machine
│   ├── discovery.py           # Ticker discovery within thesis universe
│   ├── signals.py             # Signal generation + confidence scoring
│   ├── execution.py           # Order dispatch, approval workflow
│   ├── risk.py                # Exposure, drawdown, kill switch, pre-trade checks
│   ├── scheduler.py           # APScheduler task management
│   ├── analytics.py           # Sharpe, benchmarks, VaR, correlation
│   ├── principles.py          # Self-learning rules engine
│   ├── congress.py            # Congress trades sentiment signal
│   ├── news.py                # News scanning, thesis relevance scoring
│   └── pricing.py             # Price service: Schwab streaming + yfinance fallback
├── broker/
│   ├── __init__.py
│   ├── base.py                # Abstract broker interface
│   ├── mock.py                # Mock broker: fake fills, yfinance prices
│   └── schwab.py              # Schwab adapter via schwab-py
├── bot/
│   ├── __init__.py
│   └── telegram.py            # Telegram bot: signal notifications, approve/reject
├── db/
│   ├── __init__.py
│   ├── database.py            # Connection management, WAL mode, row factory
│   ├── schema.sql             # Full schema (20+ tables)
│   ├── migrations/            # Versioned schema migrations
│   └── seed.py                # Import from money_journal
├── api/
│   ├── __init__.py
│   ├── app.py                 # FastAPI application, CORS, lifespan
│   ├── routes/
│   │   ├── fund.py            # /api/fund/* — portfolio, positions, exposure, NAV
│   │   ├── theses.py          # /api/fund/theses — thesis ingest and status
│   │   ├── signals.py         # /api/fund/signals — queue, approve, reject
│   │   ├── trades.py          # /api/fund/trades — execution history
│   │   ├── performance.py     # /api/fund/performance, benchmark, drawdown
│   │   ├── risk.py            # /api/fund/risk, stress-test, correlation
│   │   └── admin.py           # /api/admin/* — kill switch, mode switch
│   └── websocket.py           # /ws/prices real-time price feed
├── dashboard/
│   ├── static/
│   │   ├── css/
│   │   │   └── moves.css      # Notion-inspired theme
│   │   ├── js/
│   │   │   ├── app.js         # Main app: fetch, render, WebSocket, state
│   │   │   ├── charts.js      # Canvas charts: performance, drawdown, gauges
│   │   │   └── utils.js       # Formatting, color helpers
│   │   └── assets/
│   └── templates/
│       └── index.html         # Single-page dashboard (Jinja2)
├── config/
│   ├── __init__.py
│   ├── settings.py            # Environment-based config, mode switching
│   └── .env.example           # SCHWAB_APP_KEY, SCHWAB_SECRET, TELEGRAM_TOKEN, etc.
├── tests/
│   ├── conftest.py            # Shared fixtures
│   ├── test_thesis.py
│   ├── test_discovery.py
│   ├── test_signals.py
│   ├── test_broker_mock.py
│   ├── test_risk.py
│   ├── test_pricing.py
│   ├── test_analytics.py
│   ├── test_principles.py
│   ├── test_telegram.py
│   └── test_api.py
├── data/                      # Gitignored
│   ├── moves_mock.db
│   └── moves_live.db
├── requirements.txt
└── pyproject.toml
```

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.12+ | Type hints throughout |
| API | FastAPI + WebSocket | Uvicorn server |
| Database | SQLite (WAL mode) | 20+ tables, separate mock/live DBs |
| Broker | schwab-py v1.5.0 | Official Schwab API, OAuth, streaming |
| Prices | Schwab streaming (live) + yfinance (fallback/mock) | 15s cache TTL |
| Bot | python-telegram-bot | Signal notifications, approve/reject/ignore |
| Scheduler | APScheduler | Cron-based task scheduling |
| Frontend | Vanilla JS, HTML, CSS | No framework. Inter font. |
| Testing | pytest | TDD throughout |
| News | Web search APIs | Thesis relevance scoring |

## Modes

| Mode | Database | Broker | Prices | Purpose |
|------|----------|--------|--------|---------|
| **Mock** | `data/moves_mock.db` | `broker/mock.py` | yfinance | Fake fills at market price. For development and testing. |
| **Live** | `data/moves_live.db` | `broker/schwab.py` | Schwab streaming | Real money via Schwab API. Real trades. |

Separate databases. No cross-contamination. Mode is set via environment variable and displayed on dashboard.

---

## Thesis Engine

### Thesis Ingest

money_thoughts pushes theses to money_moves via the API:

```
POST /api/fund/theses
{
  "title": "AI infrastructure spending accelerates",
  "thesis_text": "Hyperscalers will increase capex 30%+ in 2026...",
  "strategy": "long",
  "symbols": ["NVDA", "AVGO", "MRVL"],
  "universe_keywords": ["AI chips", "datacenter", "GPU"],
  "validation_criteria": ["capex guidance increases", "AI revenue growth >50%"],
  "failure_criteria": ["capex cuts", "GPU oversupply signals"],
  "horizon": "6m",
  "conviction": 0.8
}
```

### Thesis State Machine

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

### Thesis Versioning

Every status change creates a `thesis_versions` row:

| Field | Type | Description |
|-------|------|-------------|
| thesis_id | INTEGER | FK to theses |
| timestamp | TEXT | ISO 8601 |
| old_status | TEXT | Previous status |
| new_status | TEXT | New status |
| reason | TEXT | Why the change occurred |
| evidence | TEXT | Supporting data (news URL, metric, etc.) |

### Ticker Discovery

After receiving a thesis, the engine autonomously discovers additional tickers within the thesis universe:

1. Use `universe_keywords` to search for related companies
2. Filter by market cap, sector, and relevance
3. Add discovered tickers to thesis `symbols[]`
4. Generate evaluation signals for new tickers
5. Track discovery source for audit

---

## Signal Engine

### Signal Model

```sql
signals (
  id            INTEGER PRIMARY KEY,
  action        TEXT NOT NULL,        -- BUY / SELL / SHORT / COVER
  symbol        TEXT NOT NULL,
  thesis_id     INTEGER REFERENCES theses(id),
  confidence    REAL NOT NULL,        -- 0.0 to 1.0, scored by engine
  source        TEXT NOT NULL,        -- thesis_update / price_trigger / news_event /
                                      -- congress_trade / discovery / manual
  horizon       TEXT,                 -- 1d / 1w / 1m / 3m / 6m / 1y
  reasoning     TEXT,                 -- LLM-generated explanation
  size_pct      REAL,                 -- suggested position size as % of NAV
  funding_plan  TEXT,                 -- JSON: which lot to sell, tax impact
  status        TEXT DEFAULT 'pending', -- pending / approved / rejected / ignored /
                                        -- executed / expired / cancelled
  telegram_msg_id TEXT,               -- Telegram message ID for tracking response
  created_at    TEXT DEFAULT (datetime('now')),
  decided_at    TEXT,                 -- when user approved/rejected
  expired_at    TEXT                  -- when 24h timeout hit
)
```

### Signal Lifecycle

```
LLM generates signal
       │
       ▼
   [pending]  ──► Telegram notification sent to user
       │
       ├── User taps "Approve"  ──► [approved] ──► Execute via broker ──► [executed]
       │
       ├── User taps "Reject"   ──► [rejected] ──► Record to what_if (engaged rejection)
       │
       ├── 24h passes, no response ──► [ignored] ──► Record to what_if (passive ignore)
       │
       └── Kill switch / risk limit ──► [cancelled]
```

### Confidence Scoring

LLM generates a raw confidence score. The engine adjusts it through multiple layers:

```python
def score_confidence(signal, thesis, principles, profile):
    # 1. Base: LLM raw confidence
    score = signal.raw_confidence

    # 2. Thesis strength modifier
    score *= thesis_strength_multiplier(thesis.status)
    # active=1.0, strengthening=1.1, confirmed=1.2, weakening=0.6, invalidated=0.0

    # 3. Principles engine adjustment
    for principle in principles.match(signal):
        score += principle.weight  # +0.05 for validated principles, -0.05 for unvalidated

    # 4. Domain expertise weighting (configurable profile)
    if signal.domain in profile.expertise_domains:
        score *= profile.domain_boost  # e.g., 1.15 for AI/SW/HW

    # 5. Source historical accuracy
    source_accuracy = signal_scores.get_accuracy(signal.source)
    score *= source_accuracy_multiplier(source_accuracy)

    # 6. Clamp to [0.0, 1.0]
    return max(0.0, min(1.0, score))
```

### Domain Expertise Profile

Configurable in settings, not hardcoded:

```python
EXPERTISE_PROFILE = {
    "domains": ["AI", "semiconductors", "software", "hardware"],
    "domain_boost": 1.15,        # 15% confidence boost for domain signals
    "out_of_domain_penalty": 0.9  # 10% penalty outside expertise
}
```

### Signal Sources

| Source | Trigger | Typical Confidence |
|--------|---------|-------------------|
| `thesis_update` | Thesis status change | 0.6 - 0.9 |
| `price_trigger` | Price hits target/stop | 0.7 - 0.95 |
| `news_event` | Thesis-relevant news detected | 0.4 - 0.8 |
| `congress_trade` | Politician trade overlap | 0.3 - 0.5 |
| `discovery` | New ticker found in thesis universe | 0.3 - 0.6 |
| `manual` | User-initiated via API | User-specified |

### Funding Plan Intelligence

For BUY signals, the engine computes a funding plan:

```json
{
  "action": "BUY",
  "symbol": "NVDA",
  "shares": 50,
  "estimated_cost": 6500.00,
  "funding": {
    "available_cash": 2000.00,
    "sell_lot": {
      "symbol": "INTC",
      "lot_id": 42,
      "shares": 100,
      "cost_basis": 5200.00,
      "current_value": 4800.00,
      "realized_loss": -400.00,
      "holding_period": "long_term",
      "tax_impact": "harvest $400 long-term loss"
    },
    "net_cost_after_tax_benefit": 6100.00
  }
}
```

---

## Telegram Bot

### Notification Format

When a signal is generated, the bot sends:

```
📊 NEW SIGNAL: BUY NVDA

Confidence: 0.82
Thesis: AI infrastructure spending accelerates
Reasoning: Hyperscaler capex guidance raised 35% avg...

Size: 2.5% of NAV (~$6,500)
Funding: Sell INTC lot #42 (harvest $400 loss)

Current: $129.50
Target: $155.00 (+19.7%)
Stop: $115.00 (-11.2%)

[✅ Approve]  [❌ Reject]
```

### User Response Flow

| Action | Button | Effect |
|--------|--------|--------|
| **Approve** | Tap "Approve" | Execute trade via broker, record to trades table |
| **Reject** | Tap "Reject" | Record to what_if with `decision = 'rejected'` |
| **Ignore** | No response for 24h | Record to what_if with `decision = 'ignored'`, separate tracking |

### Reject vs Ignore Distinction

This is a key design choice. Rejections indicate active engagement — the user evaluated the signal and disagreed. Ignores indicate passive disengagement — the user didn't care enough to respond. These are tracked separately in what_if:

| Decision | Meaning | Tracked As |
|----------|---------|------------|
| Rejected | "I looked at this and disagree" | `what_if.decision = 'rejected'` |
| Ignored | "I didn't engage with this" | `what_if.decision = 'ignored'` |

Over time, this reveals:
- **Reject accuracy**: Are the user's active disagreements correct?
- **Ignore cost**: How much alpha is lost to inattention?
- **Engagement quality**: Does engagement correlate with better decisions?

### Additional Bot Commands

```
/status        — Current NAV, return, exposure
/positions     — Open positions summary
/killswitch    — Emergency: halt all trading
/mode          — Show current mode (mock/live)
```

---

## Execution

### Broker Interface

```python
class Broker(ABC):
    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_account_balance(self) -> AccountBalance: ...

    @abstractmethod
    async def place_order(self, order: Order) -> OrderResult: ...

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderStatus: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def preview_order(self, order: Order) -> OrderPreview: ...
```

### Mock Broker

- Instant fills at current yfinance price
- FIFO lot accounting: create lot on buy, consume oldest lot on sell
- Cash management: track available cash, reject orders exceeding balance
- Configurable slippage model (default: 0 bps)
- All trades recorded to `trades` and `orders` tables

### Schwab Broker

- OAuth 2.0 via schwab-py with automatic token refresh
- Order preview before execution (Schwab's preview endpoint)
- Real-time streaming prices via Schwab WebSocket
- Position reconciliation: DB vs Schwab, flag discrepancies
- Rate limit handling, retry logic, error mapping

### Account Map

| Account | Broker | Purpose | Automation |
|---------|--------|---------|------------|
| Individual Brokerage | Schwab | META RSU holding + active trading | Full (via API) |
| Brokerage | Fidelity | Active trading | Manual (positions tracked in DB) |
| Roth IRA (me) | Fidelity | Backdoor Roth | Manual |
| Roth IRA (spouse) | Fidelity | Spousal backdoor Roth | Manual |
| Meta 401(k) | Fidelity | Index funds | Manual |
| QCOM 401(k) | Fidelity | Contrafund + DFA intl | Manual |

Phase 1: Schwab only (API-enabled). Fidelity accounts tracked but not automated.

---

## Risk Management

### Pre-Trade Checks

Every signal passes through risk checks before execution:

```python
def pre_trade_check(signal, portfolio, risk_limits):
    checks = [
        check_kill_switch(),                    # trading halted?
        check_position_size(signal, portfolio), # > max position % of NAV?
        check_sector_concentration(signal),     # > max sector %?
        check_gross_exposure(portfolio),        # > max gross exposure?
        check_net_exposure(portfolio),          # outside net exposure band?
        check_trading_window(signal),           # META outside window?
        check_drawdown_limit(portfolio),        # in drawdown lockout?
        check_daily_loss_limit(portfolio),      # daily loss exceeded?
    ]
    return all(checks)
```

### Risk Limits (Configurable)

| Limit | Default | Description |
|-------|---------|-------------|
| max_position_pct | 15% | Max single position as % of NAV |
| max_sector_pct | 35% | Max sector concentration |
| max_gross_exposure | 150% | Max long + short as % of NAV |
| net_exposure_band | [-30%, 130%] | Allowable net exposure range |
| max_drawdown | 20% | Halt trading if breached |
| daily_loss_limit | 3% | Halt new longs if daily loss exceeds |

### Kill Switch

Emergency halt. Activated manually (dashboard or Telegram) or automatically (drawdown limit breach).

```sql
kill_switch (
  id            INTEGER PRIMARY KEY,
  active        BOOLEAN DEFAULT FALSE,
  activated_at  TEXT,
  reason        TEXT,
  activated_by  TEXT,    -- 'user', 'risk_engine', 'telegram'
  deactivated_at TEXT
)
```

When active:
- No new positions opened
- Close-only orders permitted
- Dashboard shows red banner
- Telegram sends alert

### Drawdown Tracking

```sql
drawdown_events (
  id            INTEGER PRIMARY KEY,
  peak_date     TEXT,
  peak_value    REAL,
  trough_date   TEXT,
  trough_value  REAL,
  drawdown_pct  REAL,
  recovery_date TEXT,    -- NULL if still underwater
  days_underwater INTEGER
)
```

### META Trading Window Enforcement

```sql
trading_windows (
  id      INTEGER PRIMARY KEY,
  symbol  TEXT DEFAULT 'META',
  opens   TEXT,     -- ISO date
  closes  TEXT,     -- ISO date
  notes   TEXT
)
```

- Engine blocks META signals outside open windows
- Dashboard shows countdown: "META window closes in N days"
- Telegram warns when window is closing

---

## Principles Engine

Self-learning investment rules, ported from money_journal.

### Principle Model

```sql
principles (
  id              INTEGER PRIMARY KEY,
  text            TEXT NOT NULL,
  category        TEXT,          -- 'risk', 'timing', 'conviction', 'domain', 'process'
  origin          TEXT,          -- 'journal_import', 'pattern_discovery', 'user_input',
                                --  'outcome_analysis'
  validated_count INTEGER DEFAULT 0,
  invalidated_count INTEGER DEFAULT 0,
  weight          REAL DEFAULT 0.05,  -- confidence adjustment magnitude
  active          BOOLEAN DEFAULT TRUE,
  last_applied    TEXT,
  created_at      TEXT DEFAULT (datetime('now'))
)
```

### Seed Principles (from money_journal)

- P1: Insider experience is high-signal for conviction
- P2: Stressful cultures often correlate with shareholder returns
- P3: Domain expertise creates durable edge — lean into it
- P4: Avoid legacy tech with rigid structures

### Lifecycle

1. **Applied** — during confidence scoring, matching principles adjust score
2. **Validated** — trade outcome positive after N days, increment `validated_count`
3. **Invalidated** — trade outcome negative, increment `invalidated_count`
4. **Deactivated** — if `invalidated_count > validated_count * 2`, flag for review
5. **Discovered** — analytics engine detects new pattern, suggests as principle

### Pattern Discovery

At 30/60/90 day marks after each trade:
- Check which principles were applied to the original signal
- Win: increment validated_count, increase weight
- Loss: increment invalidated_count, decrease weight
- Analyze win rates by: signal source, sector, thesis age, domain
- Suggest new principles when patterns emerge (surfaced on dashboard)

---

## What-If Tracking

```sql
what_if (
  id              INTEGER PRIMARY KEY,
  signal_id       INTEGER REFERENCES signals(id),
  decision        TEXT NOT NULL,     -- 'rejected' or 'ignored'
  price_at_pass   REAL NOT NULL,
  current_price   REAL,
  hypothetical_pnl REAL,
  hypothetical_pnl_pct REAL,
  days_since      INTEGER,
  updated_at      TEXT
)
```

Updated daily. Computes:
- **Pass accuracy**: % of passed signals where passing was correct (< 5% gain for buys)
- **Reject accuracy**: % of rejections that were correct decisions
- **Ignore cost**: total alpha lost to inattention
- **Engagement quality**: does active engagement (reject) correlate with better decisions than passive (ignore)?

---

## Congress Trades Signal

```sql
congress_trades (
  id            INTEGER PRIMARY KEY,
  politician    TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  action        TEXT NOT NULL,     -- BUY / SELL
  amount_range  TEXT,              -- '$1K-$15K', '$50K-$100K', etc.
  date_filed    TEXT,
  date_traded   TEXT,
  source_url    TEXT,
  created_at    TEXT DEFAULT (datetime('now'))
)
```

- Daily scrape from Quiver Quantitative / Capitol Trades
- Cross-reference against positions and thesis symbols
- Generate low-confidence signals on overlap (source: `congress_trade`)
- 45-day filing lag — treat as sentiment, not timing
- Dashboard section: recent trades, portfolio overlap flags

---

## Database Schema

### Complete Table List

```sql
-- POSITIONS & LOTS
positions (id, account_id, symbol, shares, avg_cost, side, strategy, thesis_id, created_at, updated_at)
lots (id, position_id, account_id, symbol, shares, cost_basis, acquired_date, source, holding_period, closed_date, closed_price)

-- THESIS ENGINE
theses (id, title, thesis_text, strategy, status, symbols, universe_keywords, validation_criteria, failure_criteria, horizon, conviction, source_module, created_at, updated_at)
thesis_versions (id, thesis_id, timestamp, old_status, new_status, reason, evidence)
thesis_news (id, thesis_id, headline, url, sentiment, relevance_score, timestamp)

-- SIGNAL ENGINE
signals (id, action, symbol, thesis_id, confidence, source, horizon, reasoning, size_pct, funding_plan, status, telegram_msg_id, created_at, decided_at, expired_at)
signal_scores (id, source_type, total, wins, losses, avg_return, last_updated)

-- EXECUTION
trades (id, signal_id, symbol, action, shares, price, total_value, lot_id, fees, broker, account_id, realized_pnl, timestamp)
orders (id, signal_id, order_type, limit_price, status, schwab_order_id, submitted_at, filled_at, cancelled_at, error_message)

-- PORTFOLIO
portfolio_value (id, date, total_value, long_value, short_value, cash, cost_basis, daily_return_pct)
exposure_snapshots (id, date, gross_exposure, net_exposure, long_pct, short_pct, by_sector, by_thesis)

-- RISK
risk_limits (id, limit_type, value, enabled, updated_at)
kill_switch (id, active, activated_at, reason, activated_by, deactivated_at)
drawdown_events (id, peak_date, peak_value, trough_date, trough_value, drawdown_pct, recovery_date, days_underwater)

-- INTELLIGENCE
principles (id, text, category, origin, validated_count, invalidated_count, weight, active, last_applied, created_at)
congress_trades (id, politician, symbol, action, amount_range, date_filed, date_traded, source_url, created_at)
what_if (id, signal_id, decision, price_at_pass, current_price, hypothetical_pnl, hypothetical_pnl_pct, days_since, updated_at)

-- SCHEDULING
scheduled_tasks (id, name, cron_expression, last_run, next_run, status, error_log)

-- AUDIT
audit_log (id, timestamp, actor, action, details, entity_type, entity_id)

-- ACCOUNTS
accounts (id, name, broker, account_type, account_hash, purpose, trading_restrictions, active)
trading_windows (id, symbol, opens, closes, notes)
```

### Database Rules

- WAL mode enabled for concurrent reads during WebSocket streaming
- Row factory returns dictionaries (not tuples)
- All timestamps in ISO 8601 UTC
- JSON columns stored as TEXT, parsed on read
- Foreign keys enforced
- Separate databases for mock and live modes

---

## API Endpoints

### Portfolio & Positions

```
GET  /api/fund/status              — NAV, return %, mode, active strategies
GET  /api/fund/positions           — Open positions with live prices, P/L
GET  /api/fund/position/{ticker}   — Detail: lots, sparkline data, thesis link
GET  /api/fund/exposure            — Long/short/net breakdown, by sector, by thesis
```

### Theses

```
GET  /api/fund/theses              — Active theses with status badges
POST /api/fund/theses              — Ingest thesis from money_thoughts
PUT  /api/fund/theses/{id}         — Update thesis (status, criteria)
```

### Signals

```
GET  /api/fund/signals             — Signal queue: pending, recent history
POST /api/fund/signals/{id}/approve — Approve signal for execution
POST /api/fund/signals/{id}/reject  — Reject signal, record to what_if
```

### Trades & Performance

```
GET  /api/fund/trades              — Execution history, last N trades
GET  /api/fund/performance         — Daily NAV time series
GET  /api/fund/benchmark           — SPY/QQQ/IWM comparison data
GET  /api/fund/drawdown            — Underwater analysis, max DD, days
```

### Risk & Analytics

```
GET  /api/fund/risk                — VaR, stress test, concentration
GET  /api/fund/correlation         — Thesis-to-thesis correlation matrix
GET  /api/fund/heatmap             — Position heatmap: value + P/L color
GET  /api/fund/macro-indicators    — Economic indicators with 1d change
```

### Intelligence (Unique)

```
GET  /api/fund/congress-trades     — Recent filings, portfolio overlap
GET  /api/fund/principles          — Active principles, validation counts
GET  /api/fund/what-if             — Passed signals, hypothetical P/L
```

### Admin

```
POST /api/fund/kill-switch         — Toggle kill switch on/off
POST /api/fund/mode/{mode}         — Switch mock/live (with confirmation)
GET  /api/fund/audit-log           — Recent audit entries
```

### WebSocket

```
WS   /ws/prices                    — Real-time price stream for all positions
```

Message format:
```json
{
  "type": "price_update",
  "symbol": "NVDA",
  "price": 129.50,
  "change_pct": 2.3,
  "timestamp": "2026-02-07T15:30:00Z"
}
```

---

## Dashboard

### Design System (Notion-Inspired)

```
Colors (light — default):
  bg:        #ffffff
  bg-hover:  #f7f7f5      (Notion's warm gray)
  bg-card:   #ffffff
  border:    #e8e8e4      (very subtle, warm)
  text:      #37352f      (Notion's signature warm black)
  muted:     #9b9a97
  green:     #448361      (Notion's muted green)
  red:       #e03e3e      (Notion's red)
  blue:      #2f80ed
  accent:    #37352f      (text IS the accent)

Colors (dark):
  bg:        #191919
  bg-hover:  #252525
  bg-card:   #202020
  border:    #333333
  text:      #e0e0e0
  muted:     #7a7a7a
  green:     #448361
  red:       #e03e3e
  blue:      #2f80ed
  accent:    #e0e0e0

Font:        Inter, -apple-system, sans-serif
Mono:        IBM Plex Mono, SF Mono, monospace

Radius:      3px (subtle, not rounded)
Shadows:     none or barely-there
Cards:       bordered, not elevated
Density:     generous whitespace
```

Key vibe: content-first, no chrome, warm not cold, typography does all the work.

### Layout (Top to Bottom)

#### 1. Header
- "Money Moves" branding (Inter, bold, warm black)
- Mode badge: Mock (muted) / Live (red)
- Theme toggle (light/dark)
- Last updated timestamp (relative: "2 min ago")

#### 2. Summary Cards (2x3 grid)
| Card | Value | Subtext |
|------|-------|---------|
| NAV | $XXX,XXX | +$X,XXX today |
| Return % | +XX.X% | Since inception |
| Unrealized P/L | +$X,XXX | XX positions |
| Realized P/L | +$X,XXX | YTD |
| Cash | $XX,XXX | XX% of NAV |
| Sharpe | X.XX | Annualized |

Cards: white background, 1px warm border, no shadow. Green/red for positive/negative values.

#### 3. Macro Indicator Strip
- Horizontal scrolling row
- Economic indicators: VIX, 10Y yield, DXY, oil, gold, BTC
- Each: value + 1-day change (green up, red down)
- Subtle left/right scroll arrows

#### 4. Risk Profile Card
- 4 metrics in a row: worst-case loss, market crash impact (-20%), concentration risk, VaR (95%)
- Color-coded: green (safe), yellow (caution), red (danger)

#### 5. Thesis Panel
- Card per active thesis
- Status badge: strengthening (green), confirmed (blue), weakening (yellow), invalidated (red)
- Title, strategy (long/short), linked symbols
- Expandable: full thesis text, validation criteria, news matches

#### 6. Exposure Section
- Stacked horizontal bar: long (green) | short (red)
- Cash breakdown below
- Net exposure gauge: SVG arc from -100% to +100%, needle at current net exposure
- Color zones: red (leveraged short) → yellow (hedged) → green (balanced) → yellow (leveraged long)

#### 7. Correlation Heatmap
- Grid: thesis vs thesis
- Color scale: -1 (red) to 0 (white) to +1 (blue)
- Hover: exact correlation value
- Concentration score displayed

#### 8. Position Heatmap
- Rectangles sized by market value
- Colored by P/L %: deep red (-10%+) → light red → white (0%) → light green → deep green (+10%+)
- Hover tooltip: ticker, shares, value, P/L

#### 9. Positions Table
- Sortable columns: ticker, side (long/short badge), shares, entry price, current price (live-updating), market value, P/L $, P/L %, target/stop range bar, review countdown
- Click to expand: lot detail, price sparkline (30d canvas), thesis link
- Target/stop range bar: red stop | blue current needle | green target

#### 10. Performance Chart
- Canvas: NAV line with light gradient fill below
- Toggleable benchmark overlays: SPY (blue), QQQ (purple), IWM (yellow)
- Alpha and beta badges vs SPY
- Time range selector: 1M, 3M, 6M, 1Y, ALL

#### 11. Drawdown Analysis
- Metrics: max DD, current DD, days underwater
- Underwater chart: canvas, shaded red area below 0% line
- Per-thesis drawdown table

#### 12. Trades History
- Last 10 trades
- Columns: timestamp, action badge (BUY green, SELL red), ticker, shares, price, total, realized P/L

#### 13. Congress Trades (Unique)
- Recent filings table
- Portfolio overlap flags (highlighted rows)
- Sentiment summary: net buying vs selling in portfolio-adjacent tickers

#### 14. Principles Engine (Unique)
- Active principles list
- Each: text, category badge, validated/invalidated counts, last applied date
- Visual: validation bar (green validated, red invalidated)
- Recently discovered patterns flagged

#### 15. Footer
- Mode badge
- Last updated timestamp

### Real-Time Features

- WebSocket price streaming with green/red flash animation on update
- Auto-recalculate: unrealized P/L, NAV, exposure on each price change
- Stale data banner: yellow (> 5 min), red (> 1 hr)
- Reconnection logic with exponential backoff

### Responsive

- Tablet (<=768px): 3-column summary cards, hide secondary table columns
- Phone (<=480px): 2-column summary cards, full-width scrollable tables, 44px min touch targets

---

## Scheduler

### Scheduled Tasks

| Task | Schedule | Description |
|------|----------|-------------|
| Price update | Every 15 min, market hours (9:30-16:00 ET) | Refresh prices for all positions |
| News scan | 3x/day (8am, 2pm, 8pm ET) | Search thesis-relevant news |
| Pre-market review | 9:00 AM ET | Overnight news, pre-market movers, thesis check |
| NAV snapshot | 4:15 PM ET | Record daily portfolio value |
| Congress trades | 7:00 PM ET daily | Check for new filings |
| Stale thesis check | Monday 8:00 AM weekly | Flag theses older than 30 days |
| Exposure snapshot | Hourly, market hours | Record exposure breakdown |
| What-if update | 4:30 PM ET daily | Refresh hypothetical P/L for passed signals |
| Signal expiry check | Hourly | Expire pending signals past 24h timeout |
| Principle validation | Weekly Sunday 8:00 PM | Check 30/60/90 day trade outcomes |

### Error Handling

- Log failures to `scheduled_tasks.error_log`
- Retry logic: 3 attempts with exponential backoff
- Alert on 3+ consecutive failures (via Telegram)
- Tasks can be enabled/disabled via `scheduled_tasks.status`

---

## Analytics Engine

### Metrics Computed

| Metric | Method | Notes |
|--------|--------|-------|
| Sharpe ratio | Annualized, rf=4.5% | Per-thesis and portfolio-wide |
| Alpha | vs SPY, QQQ, IWM | Regression-based |
| Beta | vs SPY | Portfolio beta |
| Win rate | By conviction, source, thesis, domain | Percentage of profitable trades |
| Calibration | Confidence vs actual outcomes | Are high-confidence signals better? |
| Max drawdown | Peak-to-trough | With recovery tracking |
| VaR (95%) | Parametric, daily returns | Normal distribution assumption |
| Stress test | -20% market, -30% sector | Worst-case NAV impact |
| Correlation matrix | Between thesis returns | Diversification scoring |
| Pass accuracy | % of correct passes | Validated against what-if data |

---

## money_thoughts Integration

### Inbound (money_thoughts -> money_moves)

money_thoughts pushes theses via `POST /api/fund/theses`:

```json
{
  "title": "...",
  "thesis_text": "...",
  "strategy": "long",
  "symbols": ["NVDA", "AVGO"],
  "universe_keywords": ["AI chips", "datacenter"],
  "validation_criteria": [...],
  "failure_criteria": [...],
  "horizon": "6m",
  "conviction": 0.8,
  "source_module": "money_thoughts"
}
```

### Outbound (money_moves -> money_thoughts)

money_moves pushes results back to money_thoughts:

```json
{
  "thesis_id": 1,
  "status": "weakening",
  "signals_generated": 3,
  "signals_approved": 2,
  "signals_rejected": 1,
  "trades_executed": 2,
  "total_pnl": 1250.00,
  "pnl_pct": 4.2,
  "principle_learnings": [
    "P3 validated: domain expertise boost was correct for NVDA signal"
  ],
  "what_if_summary": {
    "rejected_would_have_gained": 850.00,
    "ignored_would_have_gained": 0.00
  },
  "reasoning_summary": "LLM-generated summary of thesis performance and learnings"
}
```

This enables money_thoughts to:
- Update thesis conviction based on real outcomes
- Learn from rejected/ignored signals
- Incorporate principle learnings into future thesis development

---

## Data Migration

`db/seed.py` imports from money_journal:

| Source | Target |
|--------|--------|
| `money_journal/data/journal.db` price_history | `price_history` (if table exists) |
| `money_journal/portfolio.md` holdings | `positions` + `lots` + `accounts` |
| `money_journal/memory/principles.md` | `principles` |
| `money_journal/memory/watchlist.md` triggers | `signals` (as pending manual) |
| `money_journal/memory/watchlist.md` congress | `congress_trades` |
| `money_journal/research/*.md` | `theses` (parse frontmatter + content) |
| `money_journal/portfolio.md` windows | `trading_windows` |

---

## Audit Trail

Every action in the system is recorded:

```sql
audit_log (
  id          INTEGER PRIMARY KEY,
  timestamp   TEXT DEFAULT (datetime('now')),
  actor       TEXT NOT NULL,     -- 'engine', 'user', 'scheduler', 'telegram', 'api'
  action      TEXT NOT NULL,     -- 'signal_generated', 'trade_executed', 'thesis_updated', etc.
  details     TEXT,              -- JSON with full context
  entity_type TEXT,              -- 'thesis', 'signal', 'trade', 'position', 'principle'
  entity_id   INTEGER
)
```

---

## Development Rules

1. All prices from APIs (yfinance or Schwab), never LLM-estimated
2. All metrics computed in Python, never LLM-generated
3. Every signal must have a `thesis_id`
4. Every execution must have an `audit_log` entry
5. TDD: write tests alongside implementation
6. Mock mode fully functional before live mode
7. LLM generates reasoning text only — never numbers, prices, or calculations
8. Confidence scores computed by engine formula, not raw LLM output
