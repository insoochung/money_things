# Implementation Plan

Phased build of the Money Moves execution engine. Each phase is self-contained and testable. TDD throughout.

See [spec/money_moves.md](spec/money_moves.md) for full specification.

---

## Phase 1: Foundation (Mock Mode)

**Goal:** Core engine running in simulation. Thesis engine, signal engine, mock broker, risk management, principles engine, audit trail — all testable via pytest. No dashboard, no Telegram.

### 1.1 Project Setup
- [ ] Python project structure (pyproject.toml, requirements.txt)
- [ ] Virtual environment: fastapi, uvicorn, yfinance, schwab-py, apscheduler, python-telegram-bot, pytest
- [ ] Config module: settings.py with mode switching (mock/live), .env support
- [ ] Logging setup (structured, per-module)
- [ ] Shared types: Position, Lot, Thesis, Signal, Order, Trade (dataclasses or Pydantic)

### 1.2 Database
- [ ] database.py: connection manager, WAL mode, row factory, context manager
- [ ] schema.sql: all 20+ tables (see spec/money_moves.md schema section)
- [ ] Migration system: version table + numbered SQL files
- [ ] seed.py: import from ~/workspace/money_journal/
  - Price history from data/journal.db
  - Holdings from portfolio.md -> positions + lots (parse markdown tables)
  - Principles from memory/principles.md -> principles table
  - Watchlist triggers from memory/watchlist.md -> signals (as pending manual)
  - Congress trades from memory/watchlist.md -> congress_trades table
  - Research files from research/*.md -> theses (parse frontmatter + content)
  - Accounts from portfolio.md -> accounts table
  - Trading windows from portfolio.md -> trading_windows table

### 1.3 Price Service
- [ ] pricing.py: get_price(), get_prices(), get_fundamentals(), get_history()
- [ ] Port from ~/workspace/money_journal/utils/price.py (yfinance + fallback)
- [ ] Server-side cache: 15-second TTL for real-time, 1-day for historical
- [ ] Batch updates for all positions
- [ ] Store to price_history table on fetch (if table exists)
- [ ] Backfill utility for historical data (2yr default)

### 1.4 Thesis Engine
- [ ] thesis.py: ThesisEngine class
- [ ] CRUD: create_thesis(), get_thesis(), list_theses(), update_thesis()
- [ ] State machine: active -> strengthening | confirmed | weakening | invalidated -> archived
- [ ] Versioning: every status change -> thesis_versions row
- [ ] Thesis-to-position linking via thesis.symbols[]
- [ ] Validation/failure criteria storage
- [ ] Ticker discovery: expand thesis universe using universe_keywords

### 1.5 Signal Engine
- [ ] signals.py: SignalEngine class
- [ ] Signal model: action (BUY/SELL/SHORT/COVER), symbol, thesis_id, confidence, source, horizon
- [ ] Signal queue: status flow — pending -> approved -> executed | rejected | ignored | expired
- [ ] Confidence scoring:
  - Base: LLM raw confidence
  - Thesis strength multiplier
  - Principles engine adjustment
  - Domain expertise weighting (configurable profile)
  - Source historical accuracy multiplier
- [ ] Signal sources: thesis_update, price_trigger, news_event, congress_trade, discovery, manual
- [ ] Source scoring: track wins/losses per source -> signal_scores table
- [ ] Funding plan: for BUY signals, identify lot to sell, compute tax impact

### 1.6 Mock Broker
- [ ] base.py: abstract Broker interface (get_positions, place_order, preview_order, etc.)
- [ ] mock.py: MockBroker implementation
  - Instant fills at current yfinance price
  - FIFO lot accounting: create lot on buy, consume oldest lot on sell
  - Cash management: track available cash, reject orders exceeding balance
  - Order history in orders table
  - Configurable slippage model

### 1.7 Risk Management
- [ ] risk.py: RiskManager class
- [ ] Pre-trade checks: position size, sector concentration, gross/net exposure, trading window, kill switch, drawdown limit, daily loss limit
- [ ] Drawdown monitoring: peak NAV, current NAV, max drawdown, days underwater
- [ ] Kill switch: halt all trading, close-only mode
- [ ] Exposure calculation: long, short, gross, net, by sector, by thesis

### 1.8 Principles Engine
- [ ] principles.py: PrinciplesEngine class
- [ ] Seed P1-P4 from money_journal (via seed.py)
- [ ] Apply during confidence scoring: match signal context against principle conditions
- [ ] Validate after trade outcomes: increment validated/invalidated counts
- [ ] Domain expertise detection: tag signals with domain, apply configurable boost

### 1.9 Audit Log
- [ ] Every engine action -> audit_log row
- [ ] Actor types: engine, user, scheduler, telegram, api
- [ ] Details: JSON blob with full context
- [ ] Entity tracking: entity_type + entity_id

### 1.10 Integration Tests
- [ ] End-to-end: create thesis -> generate signal -> approve -> execute via mock -> verify P/L
- [ ] Risk limit enforcement: signal rejected when limits exceeded
- [ ] Kill switch: trading halted, only close orders allowed
- [ ] Thesis lifecycle: active -> weakening (news) -> invalidated -> SELL signal generated
- [ ] Confidence scoring: verify principles and domain boost applied correctly

**Exit criteria:** `pytest` passes. Full lifecycle works: thesis -> signal -> confidence scoring -> execution -> P/L tracking -> risk enforcement -> audit trail.

---

## Phase 2: Dashboard

**Goal:** Notion-inspired web dashboard. All data sections populated from mock engine. Real-time price updates via WebSocket.

### 2.1 FastAPI Backend
- [ ] api/app.py: FastAPI app with CORS, lifespan hooks (start/stop engine)
- [ ] All REST endpoints from spec
- [ ] WebSocket endpoint: /ws/prices
- [ ] Serve dashboard static files + Jinja2 template

### 2.2 Dashboard Shell
- [ ] index.html: single-page layout
- [ ] moves.css: Notion-inspired light/dark theme with CSS variables
  - Light: #ffffff bg, #37352f text, #e8e8e4 border, #448361 green, #e03e3e red
  - Dark: #191919 bg, #e0e0e0 text, #333333 border
  - Font: Inter, IBM Plex Mono
  - Radius: 3px, no shadows, bordered cards
- [ ] Theme toggle: light/dark, persist to localStorage
- [ ] Mode badge: Mock (muted) / Live (red)
- [ ] Stale data banner: yellow > 5min, red > 1hr
- [ ] Responsive: tablet (<=768px), phone (<=480px)

### 2.3 Summary Section
- [ ] Summary cards (2x3 grid): NAV, Return %, Unrealized, Realized, Cash, Sharpe
- [ ] Macro indicator strip: horizontal scroll, VIX, 10Y, DXY, oil, gold, BTC

### 2.4 Risk and Thesis
- [ ] Risk profile card: worst-case loss, crash impact, concentration, VaR
- [ ] Thesis panel: cards with status badges (strengthening/confirmed/weakening/invalidated)
- [ ] Expandable thesis detail: full text, criteria, news matches

### 2.5 Exposure and Correlation
- [ ] Exposure bar: stacked long (green) / short (red), cash breakdown
- [ ] Net exposure gauge: SVG arc -100% to +100%, color zones, needle
- [ ] Correlation heatmap: thesis-to-thesis, color-coded -1 to +1

### 2.6 Positions
- [ ] Position heatmap: rectangles sized by value, colored by P/L %
- [ ] Positions table: sortable, all columns from spec
- [ ] Expandable rows: lot detail, 30d sparkline (canvas), thesis link
- [ ] Target/stop range bar

### 2.7 Performance and Drawdown
- [ ] Performance chart (canvas): NAV line with gradient fill
- [ ] Benchmark overlays: SPY, QQQ, IWM with checkbox toggles
- [ ] Alpha/beta badges, time range selector (1M/3M/6M/1Y/ALL)
- [ ] Drawdown section: max DD, current DD, days underwater
- [ ] Underwater chart (canvas): red shaded area below 0%
- [ ] Per-thesis drawdown table

### 2.8 Trades and Intelligence
- [ ] Trades table: last 10, action badges, all columns
- [ ] Congress trades section: recent filings, portfolio overlap flags
- [ ] Principles section: active rules, validation counts, last applied

### 2.9 Real-Time
- [ ] WebSocket connection to /ws/prices
- [ ] Price flash animation (green up, red down)
- [ ] Auto-recalculate unrealized P/L, NAV, exposure on price change
- [ ] Reconnection with exponential backoff

### 2.10 Polish
- [ ] Loading skeletons (shimmer animation)
- [ ] Error states with retry
- [ ] Empty states ("No open positions", "No trades yet")
- [ ] Footer with mode badge, last updated

**Exit criteria:** Dashboard fully renders mock portfolio. All 15 sections populated. Prices update in real-time via WebSocket. Mobile responsive. Notion-quality visual polish.

---

## Phase 3: Automation and Telegram

**Goal:** System runs autonomously. Scheduled tasks, thesis auto-validation, Telegram bot for signal approval. What-if tracking. Congress trades.

### 3.1 Scheduler
- [ ] scheduler.py: APScheduler integration
- [ ] Task registry in scheduled_tasks table
- [ ] Scheduled jobs:
  - Price update: every 15 min, market hours (9:30-16:00 ET)
  - News scan: 3x/day (8am, 2pm, 8pm ET)
  - Pre-market review: 9:00 AM ET
  - NAV snapshot: 4:15 PM ET
  - Congress trades: 7:00 PM ET daily
  - Stale thesis check: Monday 8:00 AM weekly
  - Exposure snapshot: hourly, market hours
  - What-if update: 4:30 PM ET daily
  - Signal expiry: hourly (24h timeout check)
  - Principle validation: Sunday 8:00 PM weekly
- [ ] Error handling: log failures, 3x retry with backoff, alert on consecutive failures

### 3.2 Thesis Auto-Validation
- [ ] News search against thesis validation_criteria keywords
- [ ] Score news: supporting / neutral / contradicting
- [ ] Accumulation logic: N supporting -> strengthen, N contradicting -> weaken
- [ ] Auto-generate signals on status change:
  - invalidated -> SELL for linked positions
  - strengthening + below target weight -> BUY
- [ ] Record all news matches to thesis_news table

### 3.3 Telegram Bot
- [ ] bot/telegram.py: python-telegram-bot integration
- [ ] Signal notification format: action, ticker, confidence, thesis, reasoning, size, funding plan, target/stop
- [ ] Inline buttons: Approve / Reject
- [ ] Approve flow: execute trade via broker, confirm back to user
- [ ] Reject flow: record to what_if, confirm back to user
- [ ] 24h timeout: no response -> mark as ignored, record to what_if separately
- [ ] Bot commands: /status, /positions, /killswitch, /mode
- [ ] Startup notification: "Money Moves online (Mock mode)"

### 3.4 Congress Trades
- [ ] Scrape Quiver Quantitative / Capitol Trades for new filings
- [ ] Store to congress_trades table
- [ ] Cross-reference against positions and thesis symbols
- [ ] Generate low-confidence signals on overlap (source: congress_trade)
- [ ] Dashboard section update with live data

### 3.5 Principles Engine (Active Learning)
- [ ] 30/60/90 day outcome checks for each trade
- [ ] Validate/invalidate principles based on trade outcomes
- [ ] Adjust principle weights: increase on validation, decrease on invalidation
- [ ] Pattern discovery: analyze win rates by source, sector, thesis age, domain
- [ ] Suggest new principles when patterns emerge

### 3.6 What-If Engine
- [ ] On signal rejection: record price_at_pass, decision='rejected'
- [ ] On signal ignore (24h timeout): record price_at_pass, decision='ignored'
- [ ] Daily update: refresh current_price and hypothetical_pnl
- [ ] Compute: pass accuracy, reject accuracy, ignore cost, engagement quality
- [ ] Surface to dashboard: "Missed opportunities" and "Good passes"

### 3.7 Analytics Engine
- [ ] analytics.py: AnalyticsEngine class
- [ ] Sharpe ratio: annualized, rf=4.5%, per-thesis and portfolio-wide
- [ ] Benchmark comparison: SPY, QQQ, IWM — alpha, beta, correlation
- [ ] Win rate: by conviction, source, thesis, domain
- [ ] Calibration: confidence vs actual outcomes
- [ ] VaR (95%): parametric, based on daily returns
- [ ] Stress test: -20% market crash, -30% sector crash impact
- [ ] Correlation matrix: between thesis returns

**Exit criteria:** Engine runs autonomously for 48+ hours in mock mode. Telegram bot sends signals and processes approve/reject/ignore. Theses auto-validate. NAV snapshots recorded. What-if tracking operational. No manual intervention needed.

---

## Phase 4: Live Trading

**Goal:** Live trading via Schwab API. Full production readiness. Admin controls.

### 4.1 Schwab Broker Adapter
- [ ] schwab.py: implement Broker interface using schwab-py v1.5.0
- [ ] OAuth setup: register app at developer.schwab.com, token management
- [ ] get_positions(): sync Schwab positions to database
- [ ] place_order(): market and limit orders via Schwab API
- [ ] preview_order(): use Schwab preview endpoint before execution
- [ ] get_order_status(): poll order status, update orders table
- [ ] Streaming prices: Schwab WebSocket -> pricing.py cache -> /ws/prices
- [ ] Position reconciliation: DB vs Schwab, flag discrepancies
- [ ] Error handling: API rate limits, token refresh, order rejections

### 4.2 Approval Workflow Enhancements
- [ ] Auto-approve rules (configurable): low-value trades, high-confidence, thesis-confirmed
- [ ] Signal modification: adjust size/price before approving (via Telegram reply)
- [ ] Approval audit: every approve/reject recorded with timestamp and source

### 4.3 Admin Controls
- [ ] API key authentication for admin routes
- [ ] Signal approval queue on dashboard (pending signals with thesis context)
- [ ] Risk control panel: adjust limits, toggle kill switch from dashboard
- [ ] Manual signal submission (bypass thesis engine)
- [ ] Position reconciliation view: DB vs broker, one-click sync
- [ ] Mode switch: mock <-> live (with double confirmation)

### 4.4 Trading Constraints
- [ ] META trading window enforcement: block META signals outside windows
- [ ] Dashboard warning: "META window closes in N days"
- [ ] Account routing: route orders to correct account based on symbol + action
- [ ] Lot selection: specific lot identification for tax-optimized sells

### 4.5 Production Readiness
- [ ] Run mock and live side-by-side for 2+ weeks
- [ ] Daily reconciliation: automated DB vs Schwab position check
- [ ] Database backup: daily automated backup of moves_live.db
- [ ] Health check endpoint: /api/health
- [ ] Error alerting: repeated failures trigger Telegram notification
- [ ] Start small: first live trades with minimal position sizes
- [ ] Graceful degradation: if Schwab API down, queue orders for retry

**Exit criteria:** Live trading operational via Schwab. Signals routed through Telegram approve/reject flow. Positions reconcile with broker. Kill switch works end-to-end. Daily backups running. System stable for 2+ weeks.

---

## money_thoughts Integration (Cross-Cutting)

Not a phase — integrated throughout:

- **Phase 1:** Define thesis ingest API schema. Theses can be created manually or via API.
- **Phase 2:** Dashboard shows `source_module` badge on theses (money_thoughts vs manual).
- **Phase 3:** Outbound results pushed back to money_thoughts after trade outcomes. Scheduler triggers result summaries.
- **Phase 4:** Full bidirectional flow operational in production.

---

## Phase Dependencies

```
Phase 1 (Foundation)
    ├── Phase 2 (Dashboard)      <- can start after 1.2 (database) complete
    │
    └── Phase 3 (Automation)     <- can start after Phase 1 complete
        │                           Phase 2 and 3 run in parallel
        │
        └── Phase 4 (Live)       <- requires Phase 1 + Phase 3 complete
                                    Phase 2 should be mostly done
```

## Effort Estimates

| Phase | Key Modules | Tables Added | Key Risk |
|-------|-------------|-------------|----------|
| 1 | ~15 Python files | 20+ | Schema design — get it right first |
| 2 | ~5 frontend files | 0 | Visual polish — match Notion quality |
| 3 | ~8 Python files | 0 | Telegram bot reliability, scheduler stability |
| 4 | ~5 Python files | 0 | Schwab API quirks, OAuth token management |
