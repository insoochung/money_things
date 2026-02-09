-- Money Moves schema - all tables
-- SQLite, WAL mode, foreign keys enforced

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- USERS
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    telegram_id   TEXT,
    role          TEXT DEFAULT 'user',
    settings      TEXT DEFAULT '{}',
    active        BOOLEAN DEFAULT TRUE,
    created_at    TEXT DEFAULT (datetime('now')),
    last_login    TEXT
);

-- ACCOUNTS
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    broker        TEXT NOT NULL,
    account_type  TEXT NOT NULL,
    account_hash  TEXT,
    purpose       TEXT,
    trading_restrictions TEXT,
    active        BOOLEAN DEFAULT TRUE,
    user_id       INTEGER DEFAULT 1 REFERENCES users(id)
);

-- TRADING WINDOWS
CREATE TABLE IF NOT EXISTS trading_windows (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol  TEXT DEFAULT 'META',
    opens   TEXT,
    closes  TEXT,
    notes   TEXT
);

-- POSITIONS & LOTS
CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER REFERENCES accounts(id),
    symbol      TEXT NOT NULL,
    shares      REAL NOT NULL DEFAULT 0,
    avg_cost    REAL NOT NULL DEFAULT 0,
    side        TEXT NOT NULL DEFAULT 'long',
    strategy    TEXT DEFAULT '',
    thesis_id   INTEGER,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id     INTEGER REFERENCES positions(id),
    account_id      INTEGER REFERENCES accounts(id),
    symbol          TEXT NOT NULL,
    shares          REAL NOT NULL,
    cost_basis      REAL NOT NULL,
    acquired_date   TEXT NOT NULL,
    source          TEXT DEFAULT '',
    holding_period  TEXT DEFAULT '',
    closed_date     TEXT,
    closed_price    REAL
);

-- THESIS ENGINE
CREATE TABLE IF NOT EXISTS theses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    thesis_text         TEXT DEFAULT '',
    strategy            TEXT DEFAULT 'long',
    status              TEXT DEFAULT 'active',
    symbols             TEXT DEFAULT '[]',
    universe_keywords   TEXT DEFAULT '[]',
    validation_criteria TEXT DEFAULT '[]',
    failure_criteria    TEXT DEFAULT '[]',
    horizon             TEXT DEFAULT '',
    conviction          REAL DEFAULT 0.5,
    source_module       TEXT DEFAULT '',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS thesis_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id   INTEGER NOT NULL REFERENCES theses(id),
    timestamp   TEXT DEFAULT (datetime('now')),
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    reason      TEXT DEFAULT '',
    evidence    TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS thesis_news (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id       INTEGER NOT NULL REFERENCES theses(id),
    headline        TEXT NOT NULL,
    url             TEXT DEFAULT '',
    sentiment       TEXT DEFAULT 'neutral',
    relevance_score REAL DEFAULT 0,
    timestamp       TEXT DEFAULT (datetime('now'))
);

-- SIGNAL ENGINE
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    thesis_id       INTEGER REFERENCES theses(id),
    confidence      REAL NOT NULL DEFAULT 0.5,
    source          TEXT NOT NULL DEFAULT 'manual',
    horizon         TEXT DEFAULT '',
    reasoning       TEXT DEFAULT '',
    size_pct        REAL,
    funding_plan    TEXT,
    status          TEXT DEFAULT 'pending',
    telegram_msg_id TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    decided_at      TEXT,
    expired_at      TEXT
);

CREATE TABLE IF NOT EXISTS signal_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL UNIQUE,
    total       INTEGER DEFAULT 0,
    wins        INTEGER DEFAULT 0,
    losses      INTEGER DEFAULT 0,
    avg_return  REAL DEFAULT 0,
    last_updated TEXT DEFAULT (datetime('now'))
);

-- EXECUTION
CREATE TABLE IF NOT EXISTS trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id    INTEGER REFERENCES signals(id),
    symbol       TEXT NOT NULL,
    action       TEXT NOT NULL,
    shares       REAL NOT NULL,
    price        REAL NOT NULL,
    total_value  REAL DEFAULT 0,
    lot_id       INTEGER,
    fees         REAL DEFAULT 0,
    broker       TEXT DEFAULT '',
    account_id   INTEGER REFERENCES accounts(id),
    realized_pnl REAL,
    timestamp    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id       INTEGER REFERENCES signals(id),
    order_type      TEXT DEFAULT 'market',
    symbol          TEXT DEFAULT '',
    action          TEXT DEFAULT '',
    shares          REAL DEFAULT 0,
    limit_price     REAL,
    status          TEXT DEFAULT 'pending',
    schwab_order_id TEXT,
    submitted_at    TEXT,
    filled_at       TEXT,
    cancelled_at    TEXT,
    error_message   TEXT
);

-- PORTFOLIO
CREATE TABLE IF NOT EXISTS portfolio_value (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    total_value     REAL DEFAULT 0,
    long_value      REAL DEFAULT 0,
    short_value     REAL DEFAULT 0,
    cash            REAL DEFAULT 0,
    cost_basis      REAL DEFAULT 0,
    daily_return_pct REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS exposure_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,
    gross_exposure  REAL DEFAULT 0,
    net_exposure    REAL DEFAULT 0,
    long_pct        REAL DEFAULT 0,
    short_pct       REAL DEFAULT 0,
    by_sector       TEXT DEFAULT '{}',
    by_thesis       TEXT DEFAULT '{}'
);

-- RISK
CREATE TABLE IF NOT EXISTS risk_limits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    limit_type  TEXT NOT NULL UNIQUE,
    value       REAL NOT NULL,
    enabled     BOOLEAN DEFAULT TRUE,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS kill_switch (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    active          BOOLEAN DEFAULT FALSE,
    activated_at    TEXT,
    reason          TEXT,
    activated_by    TEXT,
    deactivated_at  TEXT
);

CREATE TABLE IF NOT EXISTS drawdown_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    peak_date       TEXT,
    peak_value      REAL,
    trough_date     TEXT,
    trough_value    REAL,
    drawdown_pct    REAL,
    recovery_date   TEXT,
    days_underwater INTEGER DEFAULT 0
);

-- INTELLIGENCE
CREATE TABLE IF NOT EXISTS principles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    text                TEXT NOT NULL,
    category            TEXT DEFAULT '',
    origin              TEXT DEFAULT '',
    validated_count     INTEGER DEFAULT 0,
    invalidated_count   INTEGER DEFAULT 0,
    weight              REAL DEFAULT 0.05,
    active              BOOLEAN DEFAULT TRUE,
    last_applied        TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS congress_trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    politician  TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    action      TEXT NOT NULL,
    amount_range TEXT,
    date_filed  TEXT,
    date_traded TEXT,
    source_url  TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS what_if (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id           INTEGER REFERENCES signals(id),
    decision            TEXT NOT NULL,
    price_at_pass       REAL NOT NULL,
    current_price       REAL,
    hypothetical_pnl    REAL,
    hypothetical_pnl_pct REAL,
    days_since          INTEGER DEFAULT 0,
    updated_at          TEXT DEFAULT (datetime('now'))
);

-- SCHEDULING
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    cron_expression TEXT,
    last_run        TEXT,
    next_run        TEXT,
    status          TEXT DEFAULT 'enabled',
    error_log       TEXT,
    user_id         INTEGER DEFAULT 1 REFERENCES users(id)
);

-- AUDIT
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     TEXT DEFAULT '',
    entity_type TEXT DEFAULT '',
    entity_id   INTEGER,
    user_id     INTEGER DEFAULT 1 REFERENCES users(id)
);

-- SHARED THESES
CREATE TABLE IF NOT EXISTS shared_theses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id     INTEGER REFERENCES theses(id),
    shared_by     INTEGER REFERENCES users(id),
    shared_at     TEXT DEFAULT (datetime('now')),
    active        BOOLEAN DEFAULT TRUE
);

-- PRICE HISTORY (ported from journal.db)
CREATE TABLE IF NOT EXISTS price_history (
    symbol      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    interval    TEXT NOT NULL DEFAULT '1d',
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    volume      INTEGER,
    fetched_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (symbol, timestamp, interval)
);

CREATE INDEX IF NOT EXISTS idx_price_history_symbol ON price_history(symbol);
CREATE INDEX IF NOT EXISTS idx_price_history_timestamp ON price_history(timestamp);

-- POLITICIAN SCORES
CREATE TABLE IF NOT EXISTS politician_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    politician TEXT NOT NULL UNIQUE,
    chamber TEXT,
    party TEXT,
    state TEXT,
    committees TEXT,  -- JSON array
    total_trades INTEGER DEFAULT 0,
    win_rate REAL DEFAULT 0,
    avg_return_30d REAL DEFAULT 0,
    avg_return_60d REAL DEFAULT 0,
    avg_return_90d REAL DEFAULT 0,
    best_sectors TEXT,  -- JSON array
    trade_size_preference TEXT,
    filing_delay_avg_days REAL DEFAULT 0,
    score REAL DEFAULT 0,  -- composite 0-100
    tier TEXT DEFAULT 'unknown',  -- whale/notable/average/noise
    last_updated TEXT DEFAULT (datetime('now'))
);

-- MIGRATION TRACKING
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT DEFAULT (datetime('now')),
    description TEXT DEFAULT ''
);
