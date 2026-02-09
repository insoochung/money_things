"""Shared test fixtures for the money_moves test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MOVES_ROOT = Path(__file__).resolve().parent.parent
if str(MOVES_ROOT) not in sys.path:
    sys.path.insert(0, str(MOVES_ROOT))

import os  # noqa: E402

from db.database import Database  # noqa: E402

os.environ["MOVES_TESTING"] = "true"
os.environ["MOVES_SESSION_SECRET_KEY"] = "test-secret-key-for-testing"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a fresh test database with schema initialized."""
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    database.init_schema()
    # Add user_id columns for multi-user support if not in schema yet
    _ensure_user_id_columns(database)
    return database


def _ensure_user_id_columns(database: Database) -> None:
    """Add user_id columns to tables if they don't exist yet.

    This allows tests to run even before the schema migration agent
    has updated db/schema.sql.
    """
    conn = database.connect()

    # Create users table if not exists
    conn.execute("""
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
        )
    """)

    # Insert default user for tests
    try:
        conn.execute(
            "INSERT INTO users (id, email, name, role, active) "
            "VALUES (1, 'test@test.com', 'Test User', 'admin', TRUE)"
        )
    except Exception:
        pass  # Already exists

    # Add user_id column to all tables that need it
    tables_needing_user_id = [
        "accounts", "positions", "lots", "theses", "signals", "signal_scores",
        "trades", "orders", "portfolio_value", "exposure_snapshots",
        "risk_limits", "kill_switch", "drawdown_events", "principles",
        "what_if", "trading_windows", "scheduled_tasks", "audit_log",
    ]
    for table in tables_needing_user_id:
        try:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN user_id"
                " INTEGER DEFAULT 1 REFERENCES users(id)"
            )
        except Exception:
            pass  # Column already exists

    conn.commit()


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Database pre-populated with minimal seed data for integration-level tests."""
    # Accounts
    db.execute(
        """INSERT INTO accounts (name, broker, account_type, account_hash, purpose, user_id)
           VALUES ('Test Account', 'mock', 'individual_brokerage', '999', 'testing', 1)"""
    )
    db.connect().commit()

    # Portfolio value with cash
    db.execute(
        """INSERT INTO portfolio_value (date, total_value, cash, cost_basis, user_id)
           VALUES (date('now'), 100000, 50000, 80000, 1)"""
    )
    db.connect().commit()

    # Kill switch off
    db.execute("INSERT INTO kill_switch (active, user_id) VALUES (FALSE, 1)")
    db.connect().commit()

    # Risk limits
    limits = [
        ("max_position_pct", 0.15),
        ("max_sector_pct", 0.35),
        ("max_gross_exposure", 1.50),
        ("net_exposure_min", -0.30),
        ("net_exposure_max", 1.30),
        ("max_drawdown", 0.20),
        ("daily_loss_limit", 0.03),
    ]
    for lt, val in limits:
        db.execute(
            "INSERT INTO risk_limits (limit_type, value, user_id) VALUES (?, ?, 1)",
            (lt, val),
        )
    db.connect().commit()

    # Principles
    db.execute(
        """INSERT INTO principles (text, category, origin, validated_count, weight, user_id)
           VALUES ('Domain expertise creates durable edge',
           'domain', 'journal_import', 2, 0.05, 1)"""
    )
    db.execute(
        """INSERT INTO principles (text, category, origin, validated_count, weight, user_id)
           VALUES ('Insider experience is high-signal',
           'conviction', 'journal_import', 0, 0.05, 1)"""
    )
    db.connect().commit()

    # A test thesis
    db.execute(
        """INSERT INTO theses (title, thesis_text, strategy, status, symbols, conviction, user_id)
           VALUES ('AI infrastructure spending', 'Hyperscalers increase capex',
                   'long', 'active', '["NVDA","AVGO"]', 0.8, 1)"""
    )
    db.connect().commit()

    return db
