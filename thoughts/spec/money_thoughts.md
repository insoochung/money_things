# money_thoughts Module Specification

The conversational thesis development, research, review, and learning engine for investment decision-making. This module is the "thinking" half of a two-module system (money_thoughts + money_moves).

---

## Overview

money_thoughts is where investment ideas are born, tested, and refined. The user develops theses conversationally via Telegram (Moltbot/OpenClaw) or Claude Code CLI, researches candidates, validates hypotheses, and pushes actionable theses to money_moves for autonomous execution.

### Design Insight: Thesis-First, Not Ticker-First

The old journal system went bottom-up: ticker -> research -> idea -> synthesize. money_thoughts inverts this to top-down: **thesis -> discover tickers -> research -> validate -> push to execution**.

A thesis is a belief about the world ("inference costs will shift AI spend away from NVIDIA toward custom silicon"). Tickers are evidence for or against the thesis. This creates a natural funnel:

```
Thesis (macro belief)
  -> Discover (which companies fit?)
  -> Research (deep-dive on candidates)
  -> Idea (specific BUY/SELL with sizing)
  -> Push (send to money_moves for execution)
```

### Interface

- **Primary:** Moltbot/OpenClaw via Telegram
- **Secondary:** Claude Code CLI (terminal)
- **Not supported:** Web dashboards (that belongs to money_moves)

---

## Folder Structure

```
money_thoughts/
├── CLAUDE.md                  # System entry point
├── portfolio.md               # Current holdings with tax lots
├── spec/                      # Specification documents
│   ├── money_thoughts.md      # This file
│   ├── formats.md             # File format templates
│   ├── commands.md            # Slash command specs
│   ├── utils.md               # Python utility specs
│   └── workflows.md           # Detailed workflow definitions
├── .claude/
│   ├── settings.local.json
│   ├── agents/                # Subagent definitions
│   └── skills/                # Slash command implementations
│       ├── thesis/
│       ├── research/
│       ├── discover/
│       ├── idea/
│       ├── act/
│       ├── pass/
│       ├── pulse/
│       ├── synthesize/
│       ├── review/
│       ├── push/
│       ├── portfolio/
│       ├── refresh/
│       ├── raise-cash/
│       ├── thought/
│       ├── context/
│       └── remember/
├── memory/                    # Persistent memory layer
│   ├── session.md             # Current session state
│   ├── sessions/              # Archived past sessions
│   ├── principles.md          # Investment rules from experience
│   ├── metrics.md             # Decision quality metrics
│   └── watchlist.md           # Triggers, deadlines, stale items
├── theses/                    # Thesis-first (top of funnel)
│   ├── active/                # Theses under development/validation
│   └── archive/               # Retired or fully-executed theses
├── research/                  # Deep-dive research notes by ticker
├── ideas/                     # Active BUY/SELL ideas (tracked)
├── discovery/                 # Discovery session outputs
├── reviews/                   # Weekly/periodic reviews
├── thoughts/                  # Floating thoughts by date
├── pulse/                     # Portfolio health scan outputs
├── synthesis/                 # Portfolio-level thesis views
├── data/
│   └── journal.db             # SQLite (prices, trades, snapshots)
├── utils/                     # Python utilities
│   ├── __init__.py
│   ├── price.py               # Stock prices and fundamentals
│   ├── db.py                  # SQLite persistence layer
│   ├── charts.py              # Chart generation
│   ├── polymarket.py          # Prediction market data
│   └── metrics.py             # Decision quality metrics
└── history/                   # Archived completed items
    ├── ideas/                 # Acted/passed ideas
    ├── research/              # Old research snapshots
    └── synthesis/             # Past synthesis snapshots
```

---

## Data Model

### Source of Truth

| Data | Storage | Reason |
|------|---------|--------|
| **Prices (OHLCV)** | SQLite `price_history` | Queryable, historical |
| **Trades** | SQLite `trades` | Execution records, P&L |
| **Portfolio snapshots** | SQLite `portfolio_value` | Daily value tracking |
| **Theses** | Markdown `theses/active/` | Human reasoning, versioned |
| **Research** | Markdown `research/` | Deep-dive analysis, versioned |
| **Ideas** | Markdown `ideas/` | Specific trade recs, versioned |
| **Holdings** | Markdown `portfolio.md` | Current positions, tax lots |
| **Principles** | Markdown `memory/principles.md` | Learned rules, versioned |

### Database Schema (SQLite)

Located at `data/journal.db`. Managed by `utils/db.py`.

```sql
-- price_history: source of truth for prices
CREATE TABLE price_history (
    symbol TEXT NOT NULL,
    timestamp DATETIME NOT NULL,
    interval TEXT NOT NULL DEFAULT '1d',
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume INTEGER,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, timestamp, interval)
);

-- trades: source of truth for executions
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id TEXT,
    symbol TEXT NOT NULL,
    execution_date DATE NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('buy', 'sell')),
    shares REAL NOT NULL,
    price_per_share REAL NOT NULL,
    lot_id TEXT,
    lot_cost_basis REAL,
    broker TEXT,
    confirmation_number TEXT,
    fees REAL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- portfolio_value: daily portfolio value snapshots
CREATE TABLE portfolio_value (
    date DATE PRIMARY KEY,
    total_value REAL NOT NULL,
    total_cost_basis REAL,
    cash REAL,
    positions TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Thesis Model

A thesis is a macro-level belief about the world that drives investment decisions. It is the top of the funnel.

### Thesis File Format

**Location:** `theses/active/{slug}.md`

```markdown
---
id: T001
title: "AI inference shift away from NVIDIA"
created: 2026-02-07
updated: 2026-02-07
status: active
conviction: high
themes: [AI, semiconductors, inference]
ticker_universe: [AMD, CRDO, MRVL, AVGO]
pushed_to_moves: false
---

# T001: AI Inference Shift Away from NVIDIA

## Core Belief

As AI workloads shift from training to inference, the market will
reward inference-optimized silicon over NVIDIA's training-dominant
architecture. Custom silicon and networking plays will capture
disproportionate value.

## Supporting Evidence

1. Inference is now >60% of AI compute spend (source: research/AMD.md)
2. Custom ASICs (Google TPU, Amazon Trainium) proving cost-effective
3. Networking bottlenecks make optical interconnect critical

## Validation Criteria

Conditions that would confirm this thesis:
- [ ] AMD MI300 inference benchmarks competitive with H100
- [ ] At least 2 hyperscalers announce custom inference silicon
- [ ] NVIDIA inference revenue growth slows below training growth

## Failure Criteria

Conditions that would invalidate this thesis:
- [ ] NVIDIA maintains >80% inference market share through 2027
- [ ] Custom silicon projects get cancelled or delayed significantly
- [ ] Software ecosystem lock-in proves insurmountable

## Ticker Universe

Candidates aligned with this thesis:

| Ticker | Role | Status | Idea |
|--------|------|--------|------|
| AMD | Inference GPU alternative | Researched | #003 |
| CRDO | Optical interconnect | Researched | - |
| MRVL | Custom silicon partner | Discovered | - |
| AVGO | Networking + custom ASIC | Discovered | - |

## Principles Applied

- P3: Domain expertise creates durable edge (SW/HW codesign knowledge)
- P1: Insider experience (worked on inference optimization)

## Linked Ideas

- ideas/003-AMD-buy.md (active)

## Notes

[Free-form evolving notes from conversations]
```

### Thesis Frontmatter Fields

| Field | Required | Description |
|-------|----------|-------------|
| id | Yes | Sequential ID (`T001`, `T002`, ...) |
| title | Yes | Short thesis statement |
| created | Yes | Creation date |
| updated | Yes | Last update date |
| status | Yes | `active`, `validated`, `invalidated`, `archived` |
| conviction | Yes | `low`, `medium`, `high` |
| themes | Yes | List of thematic tags |
| ticker_universe | No | Tickers aligned with this thesis |
| pushed_to_moves | No | Whether sent to money_moves |

### Thesis Lifecycle

```
Conversation (user develops belief)
     |
     v
/thesis "AI inference shift" -----> theses/active/ai-inference-shift.md
     |                                      status: active
     |
     v
/discover (finds tickers) --------> ticker_universe updated
     |
     v
/research TICKER (deep-dive) -----> research/TICKER.md
     |                               thesis validation evidence added
     v
/idea TICKER buy (trade rec) -----> ideas/NNN-TICKER-buy.md
     |                               linked to thesis
     v
/push T001 (send to executor) ----> money_moves receives thesis + tickers
     |                               pushed_to_moves: true
     v
money_moves executes autonomously
     |
     v
Results port back (trades, P&L, reasoning summaries)
     |
     v
/review (analyze outcomes) -------> principles updated
     |                               thesis status updated
     v
Next thesis iteration informed by learned principles
```

### Thesis Status Values

| Status | Meaning |
|--------|---------|
| `active` | Under development, gathering evidence |
| `validated` | Sufficient evidence, ideas created |
| `invalidated` | Failure criteria met, thesis abandoned |
| `archived` | Fully executed or superseded |

---

## Commands

### Command Overview

| Command | Purpose | Modifies |
|---------|---------|----------|
| `/thesis` | Create or update investment thesis | `theses/active/` |
| `/research {symbol}` | Deep-dive research on ticker | `research/{symbol}.md` |
| `/discover` | Find tickers aligned with theses | `discovery/YYYY-MM-DD.md` |
| `/idea {symbol} {action}` | Create BUY/SELL idea | `ideas/{id}-{symbol}-{action}.md` |
| `/act {idea}` | Mark idea as executed | Moves to `history/ideas/`, SQL |
| `/pass {idea}` | Mark idea as passed | Moves to `history/ideas/` |
| `/pulse` | Portfolio health scan | `pulse/YYYY-MM-DD.md`, SQL |
| `/synthesize` | Thesis-level portfolio view | `synthesis/YYYY-MM-DD.md` |
| `/review` | Outcome review with learning | `reviews/YYYY-WXX.md` |
| `/push {thesis}` | Push thesis to money_moves | Updates thesis, sends to moves |
| `/portfolio` | View/edit holdings | `portfolio.md` |
| `/refresh [tickers]` | Update prices | `research/*.md`, `watchlist.md`, SQL |
| `/raise-cash {amount}` | Tax-optimized sell recs | None (read-only) |
| `/thought` | Capture floating thoughts | `thoughts/YYYY-MM-DD.md` |
| `/context` | Analyze friction, improve system | Various config files |
| `/remember` | Access persistent memory | `memory/*.md` |

---

### /thesis

**Purpose:** Create or update an investment thesis.

**Usage:**
```
/thesis "AI inference shift away from NVIDIA"    # Create new thesis
/thesis T001                                      # View/update existing thesis
/thesis T001 "add AMD benchmark data"            # Update with new evidence
```

**Arguments:**
- `title` (for new): Thesis statement in quotes
- `id` (for existing): Thesis ID (e.g., `T001`)
- `update` (optional): Context for update

**Behavior:**

1. **Create mode** (with title string):
   - Engage user in conversation to develop the thesis
   - Ask clarifying questions about core belief, timeframe, conviction
   - Check `memory/principles.md` for relevant principles to apply
   - Generate slug from title
   - Assign next sequential ID
   - Write to `theses/active/{slug}.md`
   - Prompt: "Want to /discover tickers for this thesis?"

2. **View mode** (with ID, no update):
   - Display thesis with current status
   - Show linked research and ideas
   - Check validation/failure criteria against current evidence

3. **Update mode** (with ID + update context):
   - Add new evidence to thesis
   - Re-evaluate conviction level
   - Update validation/failure criteria progress
   - Update `updated` date

**Files touched:** `theses/active/{slug}.md`

---

### /research {symbol}

**Purpose:** Deep-dive research on a specific ticker.

**Usage:**
```
/research AAPL
/research AAPL "focus on services growth"
```

**Arguments:**
- `symbol` (required): Stock ticker
- `focus` (optional): Specific areas to research

**Behavior:**

1. Validate ticker exists
2. Check if ticker is in any active thesis's `ticker_universe`
   - If yes: frame research in context of that thesis
   - If no: standalone research
3. Fetch fundamentals via `utils/price.py`
4. Web search for recent news
5. Check prediction markets via `utils/polymarket.py`
6. Archive existing research (if any) to `history/research/{symbol}-{date}.md`
7. Write to `research/{symbol}.md`
8. If thesis-linked: update thesis with findings

**Files touched:** `research/{symbol}.md`, optionally `theses/active/*.md`

---

### /discover

**Purpose:** Find candidate tickers aligned with active theses.

**Usage:**
```
/discover                           # Discover for all active theses
/discover T001                      # Discover for specific thesis
/discover "AI chips"                # Discover for ad-hoc theme
```

**Arguments:**
- `thesis_id` (optional): Specific thesis to discover for
- `theme` (optional): Ad-hoc theme string

**Behavior:**

1. If thesis ID provided: load that thesis
2. If theme provided: use as search context
3. If no arguments: scan all active theses for discovery
4. For each thesis/theme:
   - Extract key concepts and search terms
   - Web search for related companies
   - Filter out already-owned tickers
   - Filter out already-in-universe tickers
   - Rank by relevance to thesis
5. Write to `discovery/YYYY-MM-DD.md`
6. Prompt to update thesis `ticker_universe` with discoveries
7. Suggest: "Want to /research any of these?"

**Files touched:** `discovery/YYYY-MM-DD.md`, optionally `theses/active/*.md`

---

### /idea {symbol} {action}

**Purpose:** Create a specific BUY or SELL recommendation with position sizing.

**Usage:**
```
/idea AAPL buy
/idea AAPL buy "Entry at $180, target $220"
/idea QCOM sell "Trim to reduce concentration"
```

**Arguments:**
- `symbol` (required): Stock ticker
- `action` (required): `buy` or `sell` (NOT hold -- hold is the default)
- `context` (optional): Entry/target/stop context

**Behavior:**

1. Validate action is `buy` or `sell` (reject `hold`)
2. Check for existing research in `research/{symbol}.md`
   - If missing: run `/research` first
3. Check if ticker is in any active thesis
   - If yes: link idea to thesis
4. Read `portfolio.md` for holdings and cash
5. Read `memory/principles.md` for sizing rules
6. Prompt user for:
   - Entry condition (price level or event)
   - Target price
   - Stop price
   - Expiry date
   - Conviction level
7. Calculate position sizing based on conviction
8. For BUY: generate tax-optimized funding plan
9. Get next ID via `utils/db.get_next_idea_id()`
10. Write to `ideas/{id}-{symbol}-{action}.md`
11. If thesis-linked: update thesis `Linked Ideas` section

**Files touched:** `ideas/{id}-{symbol}-{action}.md`, optionally `theses/active/*.md`

---

### /act {idea}

**Purpose:** Mark idea as acted upon (trade executed).

**Usage:**
```
/act 001
/act 001-AAPL-buy
```

**Arguments:**
- `idea` (required): Idea ID or filename

**Behavior:**

1. Find idea file in `ideas/`
2. Validate status is `active`
3. Prompt for trade details:
   - Date executed
   - Shares
   - Price per share
   - Broker
   - Confirmation # (optional)
   - For sells: which lot(s)
4. Record trade via `utils/db.record_trade()`
5. Update idea frontmatter: `status: acted`
6. Move to `history/ideas/`
7. Prompt to update `portfolio.md`

**SQL Operations:** `record_trade()`

**Files touched:** `ideas/` -> `history/ideas/`, optionally `portfolio.md`

---

### /pass {idea}

**Purpose:** Mark idea as passed (decided not to execute).

**Usage:**
```
/pass 001
/pass 001 "Waiting for better entry"
```

**Arguments:**
- `idea` (required): Idea ID or filename
- `reason` (optional): Reason for passing

**Behavior:**

1. Find idea file in `ideas/`
2. Validate status is `active`
3. If reason not provided, prompt for it
4. Fetch current price for "what if" tracking
5. Update idea frontmatter with pass metadata
6. Move to `history/ideas/`

**Files touched:** `ideas/` -> `history/ideas/`

---

### /pulse

**Purpose:** Objective scan of all portfolio holdings with price storage.

**Usage:**
```
/pulse
```

**Behavior:**

1. Read `portfolio.md` for holdings list
2. Initialize database via `utils/db.init_db()`
3. For each unique ticker:
   - Fetch and store price via `utils/db.ensure_prices_current()`
   - Calculate change since last pulse
   - Web search for recent news
   - Flag any material events
4. Record portfolio snapshot via `utils/db.record_portfolio_value()`
5. Check active ideas in `ideas/` for trigger conditions
6. Check active theses for validation/failure criteria updates
7. Update `memory/watchlist.md`
8. Write to `pulse/YYYY-MM-DD.md`
9. Display summary with alerts

**SQL Operations:** `init_db()`, `ensure_prices_current()`, `record_portfolio_value()`

**Files touched:** `pulse/YYYY-MM-DD.md`, `memory/watchlist.md`

---

### /synthesize

**Purpose:** Thesis-level portfolio view. Now the TOP of the funnel, not a downstream aggregator.

**Usage:**
```
/synthesize
```

**Behavior:**

1. Read all active theses from `theses/active/`
2. Read all active ideas from `ideas/`
3. Read `portfolio.md` for context
4. For each thesis:
   - List linked ideas and their status
   - Show validation/failure criteria progress
   - Calculate total exposure via linked ideas
5. Identify:
   - Thesis conflicts (contradictory beliefs)
   - Concentration risk by theme
   - Unfunded theses (thesis without ideas)
   - Orphaned ideas (ideas without thesis)
6. Write to `synthesis/YYYY-MM-DD.md`

**Files touched:** `synthesis/YYYY-MM-DD.md`

---

### /review

**Purpose:** Weekly outcome tracking and learning.

**Usage:**
```
/review
/review 2026-W06
```

**Arguments:**
- `week` (optional): Specific week to review (defaults to current)

**Behavior:**

1. Determine week to review
2. Gather inputs:
   - Active theses and their progress
   - Active ideas and their triggers
   - Ideas acted/passed this week (from `history/ideas/`)
   - Current prices from SQL
3. Review:
   - Check thesis validation/failure criteria
   - Check active ideas against triggers
   - For acted ideas: calculate actual P&L from trades table
   - For passed ideas: calculate "what if" using `price_at_pass`
4. Analyze execution results received from money_moves (if any)
5. Identify patterns and lessons
6. Propose new principles or validate existing ones
7. Write to `reviews/YYYY-WXX.md`
8. Update `memory/principles.md` if new patterns found

**SQL Operations:** `get_trades_for_idea()`, `get_price_history()`

**Files touched:** `reviews/YYYY-WXX.md`, optionally `memory/principles.md`

---

### /push {thesis}

**Purpose:** Push a validated thesis with its ticker universe to money_moves for autonomous execution.

**Usage:**
```
/push T001
/push T001 --dry-run    # Preview what would be sent
```

**Arguments:**
- `thesis_id` (required): Thesis ID to push
- `--dry-run` (optional): Preview without sending

**Behavior:**

1. Load thesis from `theses/active/`
2. Validate thesis is ready:
   - Has at least one validated ticker
   - Has validation and failure criteria defined
   - Has conviction level set
   - Has at least one linked idea with entry conditions
3. Gather push payload:
   - Thesis statement and evidence
   - Ticker universe with research summaries
   - Linked ideas with entry/exit conditions
   - Validation/failure criteria
   - Position sizing guidance
   - Relevant principles
4. If `--dry-run`: display payload and exit
5. Write payload to `money_moves` integration endpoint (file-based or API)
6. Update thesis: `pushed_to_moves: true`
7. Log push event

**Integration payload format:**
```json
{
  "thesis_id": "T001",
  "title": "AI inference shift away from NVIDIA",
  "conviction": "high",
  "thesis_statement": "...",
  "validation_criteria": ["..."],
  "failure_criteria": ["..."],
  "tickers": [
    {
      "symbol": "AMD",
      "action": "buy",
      "entry_condition": "Price <= $150",
      "target_price": 200,
      "stop_price": 120,
      "position_size_pct": 5.0,
      "research_summary": "..."
    }
  ],
  "principles": ["P3: Domain expertise creates durable edge"],
  "pushed_at": "2026-02-07T15:30:00Z"
}
```

**Files touched:** `theses/active/*.md`, money_moves integration

---

### /portfolio

**Purpose:** View or edit the portfolio holdings file.

**Usage:**
```
/portfolio              # View current portfolio
/portfolio edit         # Edit portfolio interactively
```

**Behavior:**

1. If `portfolio.md` doesn't exist: prompt user to create it
2. If exists: display current holdings summary with current prices
3. If `edit`: prompt for changes

**Files touched:** `portfolio.md`

---

### /refresh [tickers]

**Purpose:** Refresh prices and fundamentals in research files and watchlist.

**Usage:**
```
/refresh                # Refresh all research + watchlist tickers
/refresh AAPL MSFT      # Refresh specific tickers only
```

**Behavior:**

1. Collect tickers from research files and watchlist (or use provided list)
2. Fetch prices and fundamentals via `utils/price.py`
3. Store to SQL via `utils/db.ensure_prices_current()`
4. Update research file price/market cap snapshot lines
5. Update `memory/watchlist.md` trigger distances
6. Display summary with significant moves

**Files touched:** `research/*.md`, `memory/watchlist.md`

---

### /raise-cash {amount}

**Purpose:** Generate tax-optimized sell recommendations to raise cash.

**Usage:**
```
/raise-cash 10000
/raise-cash 10000 "for AMD position"
```

**Behavior:**

1. Read `portfolio.md` for all lots
2. Rank lots by tax efficiency:
   - Priority 1: Lots with losses (tax loss harvesting)
   - Priority 2: Long-term gains (lower tax rate)
   - Priority 3: Short-term gains (ordinary income rate)
3. Within each priority: prefer lower conviction positions
4. Generate specific lot-level recommendations

**Files touched:** None (read-only analysis)

---

### /thought

**Purpose:** Capture and review floating thoughts.

**Usage:**
```
/thought "META earnings feel too optimistic"    # Capture
/thought                                        # Show recent (7 days)
/thought 2026-02-01                             # Show specific date
```

**Behavior:**

1. **Capture mode** (with text): append to `thoughts/YYYY-MM-DD.md`
2. **View recent** (no args): display last 7 days of thoughts
3. **View date** (with date): display thoughts from that date

**Files touched:** `thoughts/YYYY-MM-DD.md`

---

### /context

**Purpose:** Analyze conversation friction and evolve the system.

**Usage:**
```
/context                          # Analyze conversation for insights
/context "principle text"         # Capture specific principle
/context --target=skill:thesis    # Update specific skill
```

**Behavior:**

1. Scan conversation for disagreements, corrections, repeated asks
2. Categorize by system component
3. Propose targeted updates with diffs
4. Apply on user confirmation

**Files touched:** `CLAUDE.md`, `.claude/skills/`, `.claude/agents/`, `thoughts/`

---

### /remember

**Purpose:** Access and manage the persistent memory system.

**Usage:**
```
/remember                        # Show memory summary
/remember session                # Show current session
/remember principles             # Show investment principles
/remember metrics                # Show decision metrics
/remember watchlist              # Show watchlist
/remember add "principle"        # Add new principle
/remember learn                  # Analyze history for patterns
```

**Files touched:** `memory/*.md`

---

## Memory System

### Overview

The memory layer persists context, lessons, and patterns across sessions.

| File | Purpose | Updated By |
|------|---------|------------|
| `memory/session.md` | Current session state, open threads | Session start/end |
| `memory/sessions/` | Archived past sessions by date | Session archival |
| `memory/principles.md` | Investment rules from experience | `/review`, `/remember`, `/context` |
| `memory/metrics.md` | Decision quality metrics | `/review` via `utils/metrics.py` |
| `memory/watchlist.md` | Triggers, deadlines, stale research | `/pulse`, `/refresh` |

### Principles

Principles are investment rules synthesized from experience. Each tracks its origin and validation history.

**Current principles (from existing journal):**

| ID | Principle | Origin |
|----|-----------|--------|
| P1 | Insider experience is high-signal for conviction | QCOM/META thesis updates |
| P2 | Stressful cultures correlate with returns | META thesis update |
| P3 | Domain expertise creates durable edge | Discovery session |
| P4 | Avoid legacy tech with rigid structures | Discovery session |

**Principle format:**
```markdown
### P{N}: {Title}
**Origin:** {source} ({date})
**Validated:** {count} times ({examples})

{Description of the principle}
```

Principles are applied during:
- `/thesis` creation (guidance on conviction)
- `/idea` creation (sizing rules)
- `/review` analysis (pattern matching)

### Session Lifecycle

1. **Session start:** Check for open threads, show upcoming deadlines
2. **During session:** Track topics, decisions, thoughts
3. **Session end:** Archive summary, preserve continuity notes

---

## Integration with money_moves

### Communication Model

money_thoughts and money_moves communicate through a defined interface. money_thoughts is the "brain" (thesis development, research, review). money_moves is the "hands" (execution, monitoring, reporting).

### What money_thoughts SENDS to money_moves

**Push payload** (via `/push` command):
- Validated thesis with conviction level
- Ticker universe with research summaries
- Entry/exit conditions for each ticker
- Position sizing guidance
- Validation and failure criteria
- Relevant investment principles

### What money_thoughts RECEIVES from money_moves

**Execution results** (ported back after trades):
- Trade execution records (date, shares, price, broker)
- P&L calculations (realized and unrealized)
- "What if" tracking for passed ideas
- LLM-generated reasoning summaries of execution decisions
- Portfolio state changes

### Feedback Loop

```
money_thoughts                          money_moves
    |                                       |
    |--- /push T001 (thesis+tickers) ------>|
    |                                       |--- monitors prices
    |                                       |--- executes when conditions met
    |                                       |--- records trades
    |<-- execution results + reasoning -----|
    |                                       |
    |--- /review (analyze outcomes)         |
    |--- update principles                  |
    |--- refine thesis / create new one     |
    |                                       |
    |--- /push T002 (informed by T001) ---->|
```

### Integration File Format

Results from money_moves are received as structured data that money_thoughts processes during `/review`:

```yaml
# Received from money_moves
source: money_moves
thesis_id: T001
received_at: 2026-02-15T10:00:00Z
trades:
  - symbol: AMD
    action: buy
    shares: 50
    price: 148.50
    date: 2026-02-10
    reasoning: "Entry condition met ($150 target, bought at $148.50 on market dip)"
  - symbol: CRDO
    action: buy
    shares: 200
    price: 42.00
    date: 2026-02-12
    reasoning: "Thesis validation: optical interconnect demand accelerating"
portfolio_impact:
  total_invested: 15825.00
  current_value: 16200.00
  unrealized_pnl: 375.00
what_ifs:
  - symbol: MRVL
    action: pass
    price_at_pass: 85.00
    current_price: 82.00
    reasoning: "Valuation stretched, waiting for pullback"
```

---

## Workflow Diagrams

### Daily Check-in

```
/pulse                    # How are holdings doing? Store prices.
/refresh                  # Update stale prices in research files
/remember watchlist       # Check upcoming deadlines and triggers
```

### New Investment Thesis

```
/thesis "belief about the world"     # Develop thesis conversationally
/discover T001                       # Find tickers for thesis
/research TICKER                     # Deep-dive on candidates
/idea TICKER buy                     # Create specific trade rec
/push T001                           # Send to money_moves for execution
```

### Execute a Trade (Manual)

```
/act 001                  # Record trade details
                          # Updates portfolio.md
```

### Pass on an Idea

```
/pass 001 "reason"        # Record pass with reason
                          # Enables "what if" tracking
```

### Weekly Review

```
/review                   # Analyze outcomes
                          # Updates principles
                          # Processes money_moves results
```

### Raise Cash

```
/raise-cash 10000         # Tax-optimized sell recommendations
```

### Portfolio Strategy (Thesis-Level View)

```
/synthesize               # See all theses, their ideas, exposure
                          # Identify conflicts and gaps
```

### Capture a Quick Thought

```
/thought "META earnings feel too optimistic"
```

### System Improvement

```
/context                  # Analyze friction, propose improvements
```

---

## Workflow Dependencies

```
/thesis ────────────────────────────► theses/active/{slug}.md
    │                                        │
    ▼                                        │
/discover ──────────────► discovery/         │
    │                      YYYY-MM-DD.md     │
    ▼                                        │
/research ─────────────► research/           │
    │                      {symbol}.md       │
    ▼                                        │
/idea ─────────────────► ideas/              │
    │                      {NNN}-{sym}-      │
    │                      {action}.md       │
    │                                        │
    ├───────────────────────────────────────►/push ──► money_moves
    │                                                      │
    ├──► /act ──► history/ideas/ + SQL                     │
    │                                                      │
    ├──► /pass ─► history/ideas/                           │
    │                                                      │
    └──► /synthesize ──► synthesis/                        │
                          YYYY-MM-DD.md                    │
                                                           │
         /review ◄──── results from money_moves ◄──────────┘
             │
             ▼
         reviews/YYYY-WXX.md + memory/principles.md
             │
             ▼
         Next thesis iteration (informed by outcomes)
```

---

## Current Holdings Context

The user is a Meta employee with the following holdings:

| Ticker | Shares | Broker | Source |
|--------|--------|--------|--------|
| META | 230 | Charles Schwab | RSU (multiple tax lots) |
| QCOM | 129 | E*TRADE (Morgan Stanley) | RSU |
| 401(k) | - | Fidelity | Index funds (75% US / 25% intl) |

**Trading restriction:** META can only be traded during open trading windows (typically ~2 weeks after earnings). Check `portfolio.md` for estimated window dates.

---

## Design Principles

1. **Human in the loop** -- AI assists, human decides. No autonomous trading in money_thoughts.
2. **Transparency** -- All reasoning visible in markdown, version controlled.
3. **Thesis-first** -- Everything flows from theses, not tickers. Top-down, not bottom-up.
4. **Outcome tracking** -- Learn from successes AND mistakes. Track "what ifs" for passes.
5. **Persistent memory** -- Principles and lessons persist across sessions and inform future decisions.
6. **Verified computation** -- All numbers from Python/APIs, never LLM reasoning. Run /refresh for current data.
7. **Feedback loop** -- Execution results from money_moves improve thesis quality over time.
8. **Source of truth clarity** -- SQL for quantitative data, markdown for human reasoning.
9. **Portable** -- Plain markdown + SQLite. Works with any editor. No vendor lock-in.
10. **Independent modules** -- money_thoughts and money_moves are loosely coupled. Either can function without the other, but together they form a complete system.

---

## Python Utilities

### Environment Setup

```bash
source /path/to/money_thoughts/.venv/bin/activate && python3 -c "..."
```

### Modules

| Module | Purpose | External APIs |
|--------|---------|---------------|
| `utils/price.py` | Stock prices and fundamentals | yfinance, Finnhub |
| `utils/db.py` | SQLite persistence layer | None |
| `utils/charts.py` | Chart generation | None (reads SQLite) |
| `utils/polymarket.py` | Prediction market data | Polymarket API |
| `utils/metrics.py` | Decision quality metrics | None (local computation) |

### Key Functions

```python
# Price data
from utils.price import get_price, get_prices, get_fundamentals, get_news

# Database
from utils.db import (
    init_db, ensure_prices_current, store_price,
    get_latest_price, get_price_history, backfill_prices,
    record_trade, get_trades_for_idea,
    record_portfolio_value, get_portfolio_value_history,
    get_next_idea_id, get_idea_performance, calculate_what_if,
)

# Metrics
from utils.metrics import (
    calculate_win_rate, calculate_pass_accuracy,
    calculate_calibration, calculate_timeframe_accuracy,
    analyze_by_theme, bootstrap_metrics,
)
```

---

## Error Handling

| Error | Cause | Resolution |
|-------|-------|------------|
| "Portfolio not found" | No `portfolio.md` | Run `/portfolio` to create |
| "Ticker not found" | Invalid symbol | Verify ticker symbol |
| "Thesis not found" | Wrong ID | Check `theses/active/` |
| "Idea not found" | Wrong identifier | Check `ideas/` directory |
| "Idea already closed" | Acting on history | Only active ideas can be acted/passed |
| "No active theses" | Empty `theses/active/` | Create thesis with `/thesis` |
| "Invalid action" | Using `hold` | Only `buy` or `sell` allowed |
| "Thesis not ready for push" | Missing criteria | Add validation/failure criteria |

### Graceful Degradation

- If price fetch fails: continue without prices, note in output
- If news search fails: continue with available data
- If polymarket unavailable: skip prediction market data
- If database unavailable: warn user, continue without SQL features
- If money_moves unavailable: `/push` queues payload for later delivery

---

## Migration from money_journal

money_thoughts evolves from the existing money_journal system. Key changes:

| Old (money_journal) | New (money_thoughts) | Change |
|---------------------|---------------------|--------|
| No thesis concept | `/thesis` command | Thesis-first funnel added |
| `/synthesize` aggregates ideas | `/synthesize` shows thesis-level view | Top of funnel, not bottom |
| `/discover` finds related tickers | `/discover` finds tickers for theses | Thesis-driven discovery |
| Ideas standalone | Ideas linked to theses | Traceability |
| No execution handoff | `/push` to money_moves | Separation of concerns |
| All-in-one system | Thinking module only | Focused responsibility |
| `synthesis/` directory | `theses/` directory (primary) | Thesis-first structure |

### What Stays the Same

- All existing commands (`/research`, `/idea`, `/act`, `/pass`, `/pulse`, `/review`, `/refresh`, `/raise-cash`, `/thought`, `/context`, `/remember`, `/portfolio`)
- File formats for research, ideas, pulse, reviews, thoughts
- SQLite schema and `utils/db.py`
- Memory system (session, principles, metrics, watchlist)
- Design principles (human in loop, transparency, verified computation)
