"""Shared test fixtures for the money_moves test suite.

This module provides pytest fixtures used across all money_moves tests. It handles
two critical setup concerns:

1. **Python path configuration**: Ensures the moves/ package root is on sys.path so
   that all internal imports (engine, broker, db, config) resolve correctly regardless
   of how pytest is invoked (from moves/, from repo root, or via IDE test runners).

2. **Database fixtures**: Provides two database fixtures at different levels of
   pre-population:
   - ``db``: A fresh, empty database with only the schema initialized. Used by tests
     that need a clean slate (e.g., testing seed functions, creating principles from
     scratch, migration tests).
   - ``seeded_db``: A database pre-populated with a minimal but realistic dataset
     including an account, portfolio value, kill switch (off), risk limits, principles,
     and a thesis. Used by tests that need existing data to operate against (e.g.,
     signal creation requires a thesis, risk checks require portfolio value and limits).

Both fixtures use pytest's ``tmp_path`` to create isolated SQLite files in temporary
directories, ensuring test isolation -- each test gets its own database file that is
automatically cleaned up after the test session.

Module-level constants:
    MOVES_ROOT: Absolute path to the moves/ package root, computed relative to this
        file's location (tests/ -> parent = moves/).

Fixtures:
    db: Fresh database with schema only. Scoped per-test (default function scope).
    seeded_db: Database with minimal seed data. Depends on the ``db`` fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the moves package is on the path
MOVES_ROOT = Path(__file__).resolve().parent.parent
if str(MOVES_ROOT) not in sys.path:
    sys.path.insert(0, str(MOVES_ROOT))

from db.database import Database  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Create a fresh test database with schema initialized.

    Creates a new SQLite database file in pytest's temporary directory and
    initializes all tables from db/schema.sql. The database uses WAL mode,
    dictionary row factory, and foreign key enforcement -- identical to
    production configuration.

    This fixture is function-scoped (default), so each test gets its own
    isolated database file.

    Args:
        tmp_path: Pytest built-in fixture providing a unique temporary directory
            for each test function.

    Returns:
        Database instance connected to a fresh, schema-initialized SQLite file.
        The database is ready for INSERT/SELECT operations immediately.
    """
    db_path = tmp_path / "test.db"
    database = Database(db_path)
    database.init_schema()
    return database


@pytest.fixture
def seeded_db(db: Database) -> Database:
    """Database pre-populated with minimal seed data for integration-level tests.

    Builds on the ``db`` fixture by inserting a baseline dataset that many tests
    require as preconditions. This avoids repetitive setup in individual tests
    and ensures consistent test data across the suite.

    Seed data inserted:
        - **Account**: One test account (name='Test Account', broker='mock',
          account_type='individual_brokerage', account_hash='999').
        - **Portfolio value**: $100,000 total value, $50,000 cash, $80,000 cost basis
          dated today. This provides the NAV baseline for risk calculations.
        - **Kill switch**: Inserted as inactive (active=FALSE). Tests that need the
          kill switch active must explicitly activate it.
        - **Risk limits**: All 7 standard risk limits at default values:
          max_position_pct=0.15, max_sector_pct=0.35, max_gross_exposure=1.50,
          net_exposure_min=-0.30, net_exposure_max=1.30, max_drawdown=0.20,
          daily_loss_limit=0.03.
        - **Principles**: Two principles seeded for testing principle matching and
          scoring: one for domain expertise (category='domain', validated_count=2)
          and one for conviction (category='conviction', validated_count=0).
        - **Thesis**: One active thesis ('AI infrastructure spending') with symbols
          NVDA and AVGO, conviction=0.8, strategy='long'. This allows signal
          creation tests to reference a valid thesis_id=1.

    Args:
        db: The fresh database fixture (already schema-initialized).

    Returns:
        The same Database instance, now containing the seed data described above.
        The returned object is the same ``db`` instance, just with data inserted.
    """
    # Accounts
    db.execute(
        """INSERT INTO accounts (name, broker, account_type, account_hash, purpose)
           VALUES ('Test Account', 'mock', 'individual_brokerage', '999', 'testing')"""
    )
    db.connect().commit()

    # Portfolio value with cash
    db.execute(
        """INSERT INTO portfolio_value (date, total_value, cash, cost_basis)
           VALUES (date('now'), 100000, 50000, 80000)"""
    )
    db.connect().commit()

    # Kill switch off
    db.execute("INSERT INTO kill_switch (active) VALUES (FALSE)")
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
        db.execute("INSERT INTO risk_limits (limit_type, value) VALUES (?, ?)", (lt, val))
    db.connect().commit()

    # Principles
    db.execute(
        """INSERT INTO principles (text, category, origin, validated_count, weight)
           VALUES ('Domain expertise creates durable edge', 'domain', 'journal_import', 2, 0.05)"""
    )
    db.execute(
        """INSERT INTO principles (text, category, origin, validated_count, weight)
           VALUES ('Insider experience is high-signal', 'conviction', 'journal_import', 0, 0.05)"""
    )
    db.connect().commit()

    # A test thesis
    db.execute(
        """INSERT INTO theses (title, thesis_text, strategy, status, symbols, conviction)
           VALUES ('AI infrastructure spending', 'Hyperscalers increase capex',
                   'long', 'active', '["NVDA","AVGO"]', 0.8)"""
    )
    db.connect().commit()

    return db
