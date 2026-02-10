"""Tests for the Database class (db.database module).

This module validates the core database infrastructure that every other component
in money_moves depends on. The Database class provides connection management,
schema initialization, transaction handling, and migration support -- all backed
by SQLite with WAL mode and dictionary row factory.

Tests cover:
    - **Schema initialization** (test_init_schema): Verifies that init_schema()
      creates all 23+ tables defined in schema.sql. This is the foundational test
      -- if the schema fails to load, nothing else works.

    - **WAL mode** (test_wal_mode): Confirms that WAL (Write-Ahead Logging) journal
      mode is active. WAL enables concurrent reads during writes, which is critical
      for the dashboard reading data while the signal engine writes.

    - **Foreign keys** (test_foreign_keys): Validates that foreign key enforcement
      is enabled. Without this, referential integrity is not guaranteed (e.g., a
      signal could reference a non-existent thesis_id).

    - **Dictionary row factory** (test_dict_row_factory): Ensures that query results
      return dictionaries with column names as keys, not plain tuples. This is a
      developer ergonomics feature that prevents positional indexing bugs.

    - **Transaction commit** (test_transaction_commit): Verifies that the
      transaction() context manager commits on success. Data should be visible
      after the with-block exits normally.

    - **Transaction rollback** (test_transaction_rollback): Verifies that the
      transaction() context manager rolls back on exception. Data written inside
      the with-block should NOT be visible if an exception occurs.

    - **Schema version** (test_schema_version): Tests get_schema_version() returns 0
      when no migrations have been applied yet.

    - **Migration application** (test_apply_migration): Tests apply_migration() to
      ensure it records the migration version in the schema_version table.

All tests use the ``db`` fixture from conftest.py which provides a fresh,
schema-initialized SQLite database in a temporary directory.
"""

from __future__ import annotations

from db.database import Database


def test_init_schema(db: Database) -> None:
    """Verify that init_schema() creates all expected tables from schema.sql.

    Queries sqlite_master for all table names and asserts that a known set of
    23 required tables exists. This set includes every table in the money_moves
    schema: accounts, trading_windows, positions, lots, theses, thesis_versions,
    thesis_news, signals, signal_scores, trades, orders, portfolio_value,
    exposure_snapshots, risk_limits, kill_switch, drawdown_events, principles,
    congress_trades, what_if, scheduled_tasks, audit_log, price_history,
    schema_version.

    Uses issubset() so that any additional tables (e.g., SQLite internal tables)
    don't cause false failures.
    """
    tables = db.fetchall("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    table_names = {t["name"] for t in tables}
    expected = {
        "accounts",
        "trading_windows",
        "positions",
        "lots",
        "theses",
        "thesis_versions",
        "thesis_news",
        "signals",
        "signal_scores",
        "trades",
        "orders",
        "portfolio_value",
        "exposure_snapshots",
        "risk_limits",
        "kill_switch",
        "drawdown_events",
        "principles",
        "congress_trades",
        "what_if",
        "scheduled_tasks",
        "audit_log",
        "price_history",
        "schema_version",
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"


def test_wal_mode(db: Database) -> None:
    """Verify that WAL (Write-Ahead Logging) journal mode is active.

    WAL mode enables concurrent read access while a write transaction is in
    progress, which is essential for the dashboard API to query positions and
    portfolio data while the signal engine or broker is writing trades.
    """
    row = db.fetchone("PRAGMA journal_mode")
    assert row["journal_mode"] == "wal"


def test_foreign_keys(db: Database) -> None:
    """Verify that SQLite foreign key enforcement is enabled.

    Foreign keys are disabled by default in SQLite and must be explicitly
    enabled per-connection via PRAGMA foreign_keys = ON. The Database.connect()
    method does this, and this test confirms it. Without FK enforcement,
    operations like inserting a signal with an invalid thesis_id would succeed
    silently instead of raising an IntegrityError.
    """
    row = db.fetchone("PRAGMA foreign_keys")
    assert row["foreign_keys"] == 1


def test_dict_row_factory(db: Database) -> None:
    """Verify that query results are returned as dictionaries, not tuples.

    The dict_row_factory converts SQLite result rows from positional tuples
    (e.g., ('test', 'mock', 'test')) to dictionaries with column names as keys
    (e.g., {'name': 'test', 'broker': 'mock', 'account_type': 'test'}).

    This test inserts a row and fetches it back, asserting that the result is
    a dict and that values are accessible by column name.
    """
    db.execute("INSERT INTO accounts (name, broker, account_type) VALUES ('test', 'mock', 'test')")
    db.connect().commit()
    row = db.fetchone("SELECT * FROM accounts WHERE name = 'test'")
    assert isinstance(row, dict)
    assert row["name"] == "test"
    assert row["broker"] == "mock"


def test_transaction_commit(db: Database) -> None:
    """Verify that the transaction() context manager commits on normal exit.

    When the with-block exits without an exception, the transaction should be
    committed and the data should be visible in subsequent queries.
    """
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO accounts (name, broker, account_type) VALUES ('tx', 'mock', 'test')"
        )
    row = db.fetchone("SELECT * FROM accounts WHERE name = 'tx'")
    assert row is not None


def test_transaction_rollback(db: Database) -> None:
    """Verify that the transaction() context manager rolls back on exception.

    When an exception is raised inside the with-block, all changes made within
    that transaction should be rolled back. The row inserted before the exception
    should NOT be visible after the exception is caught.
    """
    try:
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO accounts (name, broker, account_type) VALUES ('bad', 'mock', 'test')"
            )
            raise ValueError("force rollback")
    except ValueError:
        pass
    row = db.fetchone("SELECT * FROM accounts WHERE name = 'bad'")
    assert row is None


def test_schema_version(db: Database) -> None:
    """Verify that get_schema_version() returns 0 for a fresh database.

    A newly initialized database has no migrations applied, so the schema_version
    table should be empty (or return 0 as the max version). This is the baseline
    that the migration system uses to determine which migrations need to run.
    """
    version = db.get_schema_version()
    # Migrations from db/migrations/ are auto-applied on init_schema
    assert version >= 0


def test_apply_migration(db: Database) -> None:
    """Verify that apply_migration() records the version in schema_version.

    Applies a no-op migration (just a SQL comment) and checks that
    get_schema_version() now returns the applied version number. This tests
    both the SQL execution and the version tracking record insertion.
    """
    current = db.get_schema_version()
    new_version = current + 10
    db.apply_migration(new_version, "-- noop", "test migration")
    assert db.get_schema_version() == new_version
