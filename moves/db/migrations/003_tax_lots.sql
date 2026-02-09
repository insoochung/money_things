-- Tax lot tracking and wash sale watchlist tables

CREATE TABLE IF NOT EXISTS tax_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    shares REAL NOT NULL,
    cost_per_share REAL NOT NULL,
    acquired_date TEXT NOT NULL,
    sold_date TEXT,
    sold_price REAL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    lot_method TEXT DEFAULT 'fifo',
    is_wash_sale INTEGER DEFAULT 0,
    wash_sale_adjustment REAL DEFAULT 0,
    user_id INTEGER DEFAULT 1 REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS wash_sale_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    sell_date TEXT NOT NULL,
    sell_account_id INTEGER REFERENCES accounts(id),
    expiry_date TEXT NOT NULL,
    shares REAL NOT NULL,
    loss_amount REAL NOT NULL,
    triggered INTEGER DEFAULT 0,
    trigger_account_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tax_lots_symbol ON tax_lots(symbol);
CREATE INDEX IF NOT EXISTS idx_tax_lots_account ON tax_lots(account_id);
CREATE INDEX IF NOT EXISTS idx_tax_lots_sold ON tax_lots(sold_date);
CREATE INDEX IF NOT EXISTS idx_wash_sale_symbol ON wash_sale_watchlist(symbol);
CREATE INDEX IF NOT EXISTS idx_wash_sale_expiry ON wash_sale_watchlist(expiry_date);
