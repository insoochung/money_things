-- Migration 004: Add watchlist_triggers table
-- Supports entry/exit/stop_loss/take_profit triggers linked to theses

CREATE TABLE IF NOT EXISTS watchlist_triggers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER REFERENCES theses(id),
    symbol TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK(trigger_type IN ('entry','exit','stop_loss','take_profit')),
    condition TEXT NOT NULL,  -- e.g. 'price_below', 'price_above', 'pct_change'
    target_value REAL NOT NULL,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    triggered_at TEXT,
    FOREIGN KEY (thesis_id) REFERENCES theses(id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_triggers_symbol
    ON watchlist_triggers(symbol);
CREATE INDEX IF NOT EXISTS idx_watchlist_triggers_thesis
    ON watchlist_triggers(thesis_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_triggers_active
    ON watchlist_triggers(active);

-- schema_version insert handled by apply_migration()
