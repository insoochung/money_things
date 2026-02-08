"""SQLite database manager with WAL mode, dictionary row factory, and migration support.

This module provides the Database class, which is the central persistence layer for
the entire money_moves system. It wraps Python's sqlite3 module with:
    - WAL (Write-Ahead Logging) mode for concurrent read/write support
    - Foreign key enforcement for referential integrity
    - Dictionary row factory (rows returned as dicts instead of tuples)
    - Context-managed transactions with automatic commit/rollback
    - Schema initialization from schema.sql
    - Migration support with version tracking

The Database class is used by every engine module (signals, thesis, risk, principles),
the broker (mock and future live), and the seed module. It provides a simple interface
for executing queries and managing transactions without requiring callers to handle
SQLite connection details.

Connection Management:
    The Database uses lazy connection initialization -- the SQLite connection is only
    created on the first call to connect(). The connection is reused for all subsequent
    operations. check_same_thread=False is set to allow the connection to be used from
    multiple threads (e.g., FastAPI async handlers), though this requires external
    synchronization for write operations.

WAL Mode:
    WAL mode is enabled on connection to allow concurrent readers while a writer is
    active. This is important for the dashboard (reads) running simultaneously with
    the signal engine (writes).

Schema:
    The schema is loaded from db/schema.sql which defines 20+ tables including:
    accounts, trading_windows, positions, lots, theses, thesis_versions, thesis_news,
    signals, signal_scores, trades, orders, portfolio_value, exposure_snapshots,
    risk_limits, kill_switch, drawdown_events, principles, congress_trades, what_if,
    scheduled_tasks, audit_log, price_history, schema_version.

Classes:
    Database: Main database manager class.

Functions:
    dict_row_factory: Row factory that returns dictionaries instead of tuples.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def dict_row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    """Row factory that converts SQLite rows to dictionaries.

    Registered as the row_factory on the SQLite connection so that all query
    results return dictionaries with column names as keys instead of plain tuples.
    This makes code more readable and less error-prone (access by name instead of index).

    Args:
        cursor: The SQLite cursor that executed the query. Used to read column
            names from cursor.description.
        row: The raw tuple row returned by SQLite.

    Returns:
        Dictionary mapping column names to their values for this row.
    """
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


class Database:
    """SQLite database manager with WAL mode, dict rows, transactions, and migrations.

    Provides a clean interface for database operations used throughout money_moves.
    Handles connection lifecycle, schema initialization, and migration versioning.

    The database file is created automatically if it doesn't exist, and its parent
    directory is created with parents=True.

    Attributes:
        db_path: Path to the SQLite database file.
        _conn: Internal SQLite connection (None until first connect() call).
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialize the Database with a file path.

        Creates the parent directory if it doesn't exist. Does NOT create the
        connection or initialize the schema -- call connect() or init_schema()
        for that.

        Args:
            db_path: Path to the SQLite database file. Can be a string or Path object.
                The parent directory will be created if it doesn't exist.
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Get or create the SQLite connection.

        On first call, creates a new connection with:
            - Dictionary row factory (results as dicts instead of tuples)
            - WAL journal mode (concurrent reads during writes)
            - Foreign key enforcement (referential integrity)
            - check_same_thread=False (allows multi-threaded access)

        Subsequent calls return the same connection instance.

        Returns:
            The SQLite connection object.
        """
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = dict_row_factory
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self) -> None:
        """Close the database connection.

        Closes the underlying SQLite connection and resets the internal reference
        to None. Subsequent operations will create a new connection.

        Safe to call multiple times or when no connection exists.
        """
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions with automatic commit/rollback.

        Yields the connection for use within a with-block. On successful completion,
        commits the transaction. On exception, rolls back the transaction and re-raises
        the exception.

        Usage:
            with db.transaction() as conn:
                conn.execute("INSERT INTO ...")
                conn.execute("UPDATE ...")
            # Auto-committed if no exception

        Yields:
            The SQLite connection object.

        Raises:
            Any exception that occurs within the with-block (after rollback).
        """
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        """Execute a single SQL statement.

        A convenience wrapper around connection.execute() that handles connection
        management. Does NOT auto-commit -- caller must call connect().commit()
        or use the transaction() context manager.

        Args:
            sql: SQL statement to execute. Can use ? placeholders (tuple params)
                or :name placeholders (dict params).
            params: Parameters to bind to the SQL statement. Defaults to empty tuple.

        Returns:
            The SQLite cursor from the executed statement (can be used to read
            lastrowid for INSERTs or iterate over results for SELECTs).
        """
        conn = self.connect()
        return conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute a SQL statement against multiple parameter sets.

        Used for bulk operations like inserting multiple rows. More efficient than
        calling execute() in a loop.

        Args:
            sql: SQL statement with ? placeholders.
            params_list: List of parameter tuples, one per execution.

        Returns:
            The SQLite cursor from the executed statement.
        """
        conn = self.connect()
        return conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        """Execute a query and return the first result row.

        Convenience method that combines execute() and cursor.fetchone().

        Args:
            sql: SQL SELECT statement.
            params: Parameters to bind to the SQL statement.

        Returns:
            Dictionary with column names as keys for the first result row,
            or None if the query returns no results.
        """
        return self.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        """Execute a query and return all result rows.

        Convenience method that combines execute() and cursor.fetchall().

        Args:
            sql: SQL SELECT statement.
            params: Parameters to bind to the SQL statement.

        Returns:
            List of dictionaries, one per result row. Empty list if no results.
        """
        return self.execute(sql, params).fetchall()

    def init_schema(self) -> None:
        """Initialize the database schema from the schema.sql file.

        Reads the SQL schema definition from db/schema.sql and executes it using
        executescript (which handles multiple statements separated by semicolons).
        This creates all tables, indexes, and triggers defined in the schema.

        Uses CREATE TABLE IF NOT EXISTS semantics, so it's safe to call on an
        existing database.

        Side effects:
            - Creates all tables defined in schema.sql.
            - Logs a message indicating schema initialization.
        """
        schema_sql = SCHEMA_PATH.read_text()
        conn = self.connect()
        conn.executescript(schema_sql)
        logger.info("Schema initialized for %s", self.db_path)
        self._run_pending_migrations()

    def _run_pending_migrations(self) -> None:
        """Run any pending migrations from the migrations directory.

        Scans the migrations directory for SQL files named NNN_*.sql,
        checks the current schema version, and applies any migrations
        with version numbers higher than the current version.
        """
        if not MIGRATIONS_DIR.exists():
            return
        current = self.get_schema_version()
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for mf in migration_files:
            # Extract version number from filename (e.g., 002_multi_user.sql -> 2)
            try:
                version = int(mf.name.split("_", 1)[0])
            except (ValueError, IndexError):
                continue
            if version <= current:
                continue
            description = mf.stem.split("_", 1)[1] if "_" in mf.stem else mf.stem
            sql = mf.read_text()
            self.apply_migration(version, sql, description)

    def get_schema_version(self) -> int:
        """Get the current schema version number.

        Reads the maximum version from the schema_version table. Returns 0 if
        the table doesn't exist or is empty (no migrations applied yet).

        Returns:
            The highest migration version number applied, or 0 if none.
        """
        try:
            row = self.fetchone("SELECT MAX(version) as v FROM schema_version")
            return row["v"] if row and row["v"] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def apply_migration(self, version: int, sql: str, description: str = "") -> None:
        """Apply a database migration and record it in the schema_version table.

        Executes the migration SQL within a transaction. If the migration succeeds,
        records the version number and description in the schema_version table.
        If the migration fails, the transaction is rolled back.

        Args:
            version: The migration version number (must be unique, typically sequential).
            sql: The SQL statements to execute for this migration.
            description: Human-readable description of what this migration does.

        Side effects:
            - Executes the migration SQL statements.
            - Inserts a row into the schema_version table.
            - Commits the transaction (or rolls back on error).
            - Logs a message indicating the migration was applied.

        Raises:
            Any exception from the SQL execution (after rollback).
        """
        with self.transaction() as conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, description),
            )
            logger.info("Applied migration v%d: %s", version, description)
