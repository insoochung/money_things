"""SQLite persistence layer -- prices, trades, portfolio snapshots.

This module provides all database operations for the money_thoughts system.
It uses a single SQLite database file (``data/journal.db`` relative to the
project root) to store three categories of data:

1. **Price history** (``price_history`` table) -- Historical OHLCV candlestick
   data for tracked symbols. Populated by ``backfill_prices`` (from yfinance)
   and ``store_price`` / ``store_prices_bulk``. Consumed by ``utils.charts``
   for generating Obsidian-compatible chart blocks and by the ``/pulse`` skill
   for portfolio health checks.

2. **Trades** (``trades`` table) -- Records of executed buy/sell transactions,
   linked to idea IDs from the ``ideas/`` or ``history/ideas/`` directories.
   Populated by the ``/act`` skill and consumed by ``get_idea_performance``
   for P&L calculations.

3. **Portfolio snapshots** (``portfolio_value`` table) -- Daily snapshots of
   total portfolio value, cost basis, cash, and per-position detail. Populated
   by the ``/pulse`` skill and consumed by ``utils.charts.portfolio_value_chart``.

Key design decisions:
    - **SQLite** was chosen over a heavier database because money_thoughts
      runs locally in single-user mode. No concurrent write pressure.
    - **Upsert semantics** (INSERT OR REPLACE) are used throughout to make
      operations idempotent. Running ``/pulse`` or ``backfill_prices`` twice
      on the same day simply overwrites the existing row.
    - **Row factory** is set to ``sqlite3.Row`` so that query results can be
      accessed by column name and trivially converted to dicts.
    - All date/datetime parameters accept both ``date``/``datetime`` objects
      and ISO-format strings for ergonomic use from CLI skills.

The database file is created lazily by ``init_db()`` and its parent directory
(``data/``) is created automatically if missing.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

# Database location
DB_PATH = Path(__file__).parent.parent / "data" / "journal.db"


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Context manager that provides a SQLite database connection.

    Ensures the parent directory for the database file exists (creates it
    if necessary), opens a connection with ``sqlite3.Row`` row factory for
    dict-like row access, and guarantees the connection is closed when the
    context exits -- even if an exception occurs.

    Yields:
        sqlite3.Connection: An open connection to the journal database with
        ``row_factory`` set to ``sqlite3.Row``.

    Side effects:
        - Creates the ``data/`` directory if it does not exist.
        - Opens (and on exit, closes) a SQLite connection.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create all tables and indexes if they do not already exist.

    This function is idempotent -- calling it multiple times has no effect
    beyond the first invocation. It is called at the start of operations that
    require the database (e.g. chart generation, price backfill).

    Tables created:
        - ``price_history``: Stores OHLCV candlestick data.
          Primary key: ``(symbol, timestamp, interval)``.
          Columns: symbol, timestamp, interval, open, high, low, close,
          volume, fetched_at.
        - ``trades``: Records of executed buy/sell transactions.
          Auto-incrementing integer primary key.
          Columns: id, idea_id, symbol, execution_date, action, shares,
          price_per_share, lot_id, lot_cost_basis, broker,
          confirmation_number, fees, notes, created_at.
          Check constraint: action IN ('buy', 'sell').
        - ``portfolio_value``: Daily portfolio snapshots.
          Primary key: date.
          Columns: date, total_value, total_cost_basis, cash, positions
          (JSON string), created_at.

    Indexes created:
        - ``idx_price_history_symbol`` on ``price_history(symbol)``
        - ``idx_price_history_timestamp`` on ``price_history(timestamp)``
        - ``idx_trades_symbol`` on ``trades(symbol)``
        - ``idx_trades_idea`` on ``trades(idea_id)``

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes DDL statements (CREATE TABLE IF NOT EXISTS, CREATE INDEX
          IF NOT EXISTS).
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
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
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
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
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_value (
                date DATE PRIMARY KEY,
                total_value REAL NOT NULL,
                total_cost_basis REAL,
                cash REAL,
                positions TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_history_symbol
            ON price_history(symbol)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_price_history_timestamp
            ON price_history(timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_symbol
            ON trades(symbol)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_idea
            ON trades(idea_id)
        """)

        conn.commit()


def store_price(
    symbol: str,
    close: float,
    timestamp: datetime | None = None,
    interval: str = "1d",
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: int | None = None,
) -> None:
    """Store a single price data point in the price_history table.

    Uses INSERT OR REPLACE (upsert) semantics: if a row already exists for
    the given ``(symbol, timestamp, interval)`` primary key, it is overwritten.
    This makes the function idempotent -- safe to call repeatedly with the
    same data.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Will be upper-cased
            before storage.
        close: Closing price. This is the only required price field.
        timestamp: When this price was recorded. Defaults to ``datetime.now()``
            (local time, no timezone) if not provided.
        interval: Candle interval (e.g. ``"1d"``, ``"1h"``). Defaults to
            ``"1d"`` (daily). Part of the primary key.
        open_price: Opening price for the candle. Optional.
        high: Intraday high price. Optional.
        low: Intraday low price. Optional.
        volume: Trading volume for the candle. Optional.

    Side effects:
        - Opens and closes a SQLite connection.
        - Inserts or replaces one row in the ``price_history`` table.
    """
    if timestamp is None:
        timestamp = datetime.now()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO price_history
            (symbol, timestamp, interval, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (symbol.upper(), timestamp, interval, open_price, high, low, close, volume),
        )
        conn.commit()


def store_prices_bulk(prices: list[dict[str, Any]]) -> None:
    """Batch-insert multiple price data points into the price_history table.

    Uses INSERT OR REPLACE (upsert) semantics for each row. Significantly
    more efficient than calling ``store_price`` in a loop because all inserts
    share a single database connection and are committed in one transaction.

    Parameters:
        prices: A list of dicts, each with the following keys matching the
            ``price_history`` table columns:
            - ``symbol`` (str): Ticker symbol
            - ``timestamp`` (datetime): Candle timestamp
            - ``interval`` (str): Candle interval (e.g. ``"1d"``)
            - ``open`` (float | None): Opening price
            - ``high`` (float | None): High price
            - ``low`` (float | None): Low price
            - ``close`` (float): Closing price (required)
            - ``volume`` (int | None): Volume

    Side effects:
        - Opens and closes a SQLite connection.
        - Inserts or replaces multiple rows in ``price_history`` in a single
          transaction.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.executemany(
            """
            INSERT OR REPLACE INTO price_history
            (symbol, timestamp, interval, open, high, low, close, volume)
            VALUES (:symbol, :timestamp, :interval, :open, :high, :low, :close, :volume)
        """,
            prices,
        )
        conn.commit()


def get_price_on_date(symbol: str, target_date: date | str) -> dict[str, Any] | None:
    """Retrieve the closing price for a symbol on a specific calendar date.

    Looks up the most recent daily (``interval='1d'``) price record where the
    date portion of the timestamp matches ``target_date``. If multiple records
    exist for the same date (unlikely for daily data), returns the one with
    the latest timestamp.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            the query.
        target_date: The calendar date to look up. Accepts either a
            ``datetime.date`` object or a string in ``"YYYY-MM-DD"`` format.

    Returns:
        A dict with all columns from the ``price_history`` table (symbol,
        timestamp, interval, open, high, low, close, volume, fetched_at),
        or ``None`` if no record exists for that symbol and date.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM price_history
            WHERE symbol = ?
            AND date(timestamp) = ?
            AND interval = '1d'
            ORDER BY timestamp DESC
            LIMIT 1
        """,
            (symbol.upper(), target_date.isoformat()),
        )
        row = cursor.fetchone()

        if row:
            return dict(row)

    return None


def get_latest_price(symbol: str) -> dict[str, Any] | None:
    """Retrieve the most recent price record for a symbol across all intervals.

    Returns the row with the latest timestamp for the given symbol, regardless
    of candle interval. Useful for getting a quick "last known price" when
    calculating unrealised P&L.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            the query.

    Returns:
        A dict with all columns from the ``price_history`` table, or ``None``
        if no price data exists for the symbol at all.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM price_history
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT 1
        """,
            (symbol.upper(),),
        )
        row = cursor.fetchone()

        if row:
            return dict(row)

    return None


def get_last_price_timestamp(symbol: str) -> datetime | None:
    """Get the timestamp of the most recent price record for a symbol.

    Used by ``ensure_prices_current`` to determine how stale a symbol's
    price data is and whether backfilling is needed.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            the query.

    Returns:
        A ``datetime`` object representing the most recent timestamp in
        ``price_history`` for this symbol, or ``None`` if no data exists.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT MAX(timestamp) as last_ts FROM price_history
            WHERE symbol = ?
        """,
            (symbol.upper(),),
        )
        row = cursor.fetchone()

        if row and row["last_ts"]:
            return datetime.fromisoformat(row["last_ts"])

    return None


def get_price_history(
    symbol: str,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    interval: str = "1d",
) -> list[dict[str, Any]]:
    """Retrieve price history for a symbol within an optional date range.

    Returns all price records matching the given symbol and interval, ordered
    by timestamp ascending. Used by ``utils.charts`` to generate chart blocks
    and by analysis code that needs time-series data.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            the query.
        start_date: If provided, only return records on or after this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.
        end_date: If provided, only return records on or before this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.
        interval: Candle interval to filter on (default ``"1d"``). Must
            match the interval used when the data was stored.

    Returns:
        A list of dicts, each with all columns from the ``price_history``
        table. Ordered by timestamp ascending. Empty list if no matching
        records exist.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    query = """
        SELECT * FROM price_history
        WHERE symbol = ? AND interval = ?
    """
    params: list[Any] = [symbol.upper(), interval]

    if start_date:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        query += " AND date(timestamp) >= ?"
        params.append(start_date.isoformat())

    if end_date:
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        query += " AND date(timestamp) <= ?"
        params.append(end_date.isoformat())

    query += " ORDER BY timestamp ASC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def backfill_prices(
    symbol: str,
    period: str = "2y",
    interval: str = "1d",
) -> int:
    """Download historical prices from yfinance and store them in the database.

    Fetches OHLCV data for the given symbol and period, converts each row
    into a dict, and bulk-inserts them via ``store_prices_bulk``. Returns
    the count of records stored, which can be zero if the symbol is invalid
    or yfinance returns no data.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            storage.
        period: yfinance period string (e.g. ``"7d"``, ``"1mo"``, ``"2y"``).
            Defaults to ``"2y"`` (two years of daily data).
        interval: Candle interval (e.g. ``"1d"``, ``"1h"``). Defaults to
            ``"1d"``.

    Returns:
        int: Number of price records stored. Zero if the fetch failed or
        returned empty data.

    Side effects:
        - Makes one HTTP request to Yahoo Finance via yfinance.
        - Inserts/replaces rows in the ``price_history`` table.
        - Prints an error message to stdout if an exception occurs.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval)

        if hist.empty:
            return 0

        prices: list[dict[str, Any]] = []
        for timestamp, row in hist.iterrows():
            ts = timestamp.to_pydatetime()
            prices.append(
                {
                    "symbol": symbol.upper(),
                    "timestamp": ts,
                    "interval": interval,
                    "open": float(row["Open"]) if row["Open"] else None,
                    "high": float(row["High"]) if row["High"] else None,
                    "low": float(row["Low"]) if row["Low"] else None,
                    "close": float(row["Close"]),
                    "volume": int(row["Volume"]) if row["Volume"] else None,
                }
            )

        store_prices_bulk(prices)
        return len(prices)

    except Exception as e:
        print(f"Error backfilling {symbol}: {e}")
        return 0


def ensure_prices_current(symbols: list[str], max_age_hours: int = 24) -> dict[str, int]:
    """Ensure that price data for a list of symbols is up to date.

    For each symbol, checks when the last price was stored. If the data is
    older than ``max_age_hours``, or if no data exists at all, triggers a
    backfill from yfinance with an appropriately sized lookback period:

    - No data at all: backfill 2 years of daily data
    - Data older than 7 days: backfill 7 days
    - Data older than 30 days: backfill 1 month
    - Data older than 90 days: backfill 3 months
    - Data older than 90 days: backfill 2 years

    This adaptive approach avoids re-downloading years of data when only
    a few days are missing.

    Parameters:
        symbols: List of stock ticker symbols to check and potentially
            backfill (e.g. ``["AAPL", "MSFT", "GOOG"]``).
        max_age_hours: Maximum acceptable age of the most recent price
            record, in hours. Defaults to 24.

    Returns:
        A dict mapping each upper-cased symbol to the number of new price
        records added. Symbols that were already current map to 0.

    Side effects:
        - Calls ``init_db()`` to ensure tables exist.
        - May make HTTP requests to Yahoo Finance (one per stale symbol).
        - Inserts rows into the ``price_history`` table for stale symbols.
    """
    init_db()
    results: dict[str, int] = {}
    now = datetime.now()
    cutoff = now - timedelta(hours=max_age_hours)

    for symbol in symbols:
        symbol = symbol.upper()
        last_ts = get_last_price_timestamp(symbol)

        if last_ts is not None and last_ts.tzinfo is not None:
            last_ts = last_ts.replace(tzinfo=None)

        if last_ts is None:
            results[symbol] = backfill_prices(symbol, period="2y", interval="1d")
        elif last_ts < cutoff:
            days_missing = (now - last_ts).days + 1
            if days_missing <= 7:
                period = "7d"
            elif days_missing <= 30:
                period = "1mo"
            elif days_missing <= 90:
                period = "3mo"
            else:
                period = "2y"
            results[symbol] = backfill_prices(symbol, period=period, interval="1d")
        else:
            results[symbol] = 0

    return results


def record_trade(
    symbol: str,
    execution_date: date | str,
    action: str,
    shares: float,
    price_per_share: float,
    idea_id: str | None = None,
    lot_id: str | None = None,
    lot_cost_basis: float | None = None,
    broker: str | None = None,
    confirmation_number: str | None = None,
    fees: float = 0,
    notes: str | None = None,
) -> int:
    """Record an executed trade in the trades table.

    Called by the ``/act`` skill when the user confirms they have executed a
    trade recommended by an idea. The trade is linked to an idea via
    ``idea_id``, enabling P&L tracking through ``get_idea_performance``.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            storage.
        execution_date: The date the trade was executed. Accepts either a
            ``datetime.date`` object or a ``"YYYY-MM-DD"`` string.
        action: Trade direction -- must be ``"buy"`` or ``"sell"`` (case-
            insensitive; stored as lowercase). Raises ``ValueError`` if
            neither.
        shares: Number of shares traded (can be fractional).
        price_per_share: Execution price per share in USD.
        idea_id: Optional ID linking this trade to an idea file in
            ``ideas/`` or ``history/ideas/`` (e.g. ``"001"``).
        lot_id: Optional tax-lot identifier for lot-level tracking.
        lot_cost_basis: Optional cost basis for the specific lot being sold.
        broker: Optional broker name (e.g. ``"Schwab"``).
        confirmation_number: Optional broker confirmation/order number.
        fees: Transaction fees/commissions in USD. Defaults to 0.
        notes: Optional free-text notes about the trade.

    Returns:
        int: The auto-generated row ID of the inserted trade record.

    Raises:
        ValueError: If ``action`` is not ``"buy"`` or ``"sell"``.

    Side effects:
        - Opens and closes a SQLite connection.
        - Inserts one row into the ``trades`` table.
    """
    if isinstance(execution_date, str):
        execution_date = datetime.strptime(execution_date, "%Y-%m-%d").date()

    if action.lower() not in ("buy", "sell"):
        raise ValueError("action must be 'buy' or 'sell'")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO trades
            (idea_id, symbol, execution_date, action, shares, price_per_share,
             lot_id, lot_cost_basis, broker, confirmation_number, fees, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                idea_id,
                symbol.upper(),
                execution_date.isoformat(),
                action.lower(),
                shares,
                price_per_share,
                lot_id,
                lot_cost_basis,
                broker,
                confirmation_number,
                fees,
                notes,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def get_trades_for_idea(idea_id: str) -> list[dict[str, Any]]:
    """Retrieve all trades associated with a specific idea.

    Returns trades ordered by execution date ascending, which is the natural
    chronological order for P&L calculation.

    Parameters:
        idea_id: The idea identifier (e.g. ``"001"``), matching the
            ``idea_id`` column in the ``trades`` table.

    Returns:
        A list of dicts, each containing all columns from the ``trades``
        table. Empty list if no trades are linked to this idea.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM trades
            WHERE idea_id = ?
            ORDER BY execution_date ASC
        """,
            (idea_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_trades_for_symbol(symbol: str) -> list[dict[str, Any]]:
    """Retrieve all trades for a specific stock ticker.

    Returns trades ordered by execution date ascending. Useful for building
    a complete trade history for a position across multiple ideas.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Upper-cased before
            the query.

    Returns:
        A list of dicts, each containing all columns from the ``trades``
        table. Empty list if no trades exist for this symbol.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM trades
            WHERE symbol = ?
            ORDER BY execution_date ASC
        """,
            (symbol.upper(),),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_all_trades(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve all trades within an optional date range.

    Returns trades ordered by execution date ascending. When no date filters
    are provided, returns the complete trade history.

    Parameters:
        start_date: If provided, only return trades on or after this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.
        end_date: If provided, only return trades on or before this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.

    Returns:
        A list of dicts, each containing all columns from the ``trades``
        table. Ordered by execution_date ascending.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    query = "SELECT * FROM trades WHERE 1=1"
    params: list[Any] = []

    if start_date:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        query += " AND execution_date >= ?"
        params.append(start_date.isoformat())

    if end_date:
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        query += " AND execution_date <= ?"
        params.append(end_date.isoformat())

    query += " ORDER BY execution_date ASC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]


def record_portfolio_value(
    snapshot_date: date | str,
    total_value: float,
    total_cost_basis: float | None = None,
    cash: float | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> None:
    """Record a daily portfolio value snapshot.

    Uses INSERT OR REPLACE (upsert) semantics on the date primary key, so
    calling this function multiple times on the same date simply overwrites
    the previous snapshot. This is the expected pattern: the ``/pulse`` skill
    runs daily and always writes the current snapshot.

    Parameters:
        snapshot_date: The date of the snapshot. Accepts ``datetime.date``
            or ``"YYYY-MM-DD"`` string.
        total_value: Total portfolio market value in USD (positions + cash).
        total_cost_basis: Optional total cost basis across all positions.
        cash: Optional uninvested cash amount in USD.
        positions: Optional list of per-position dicts. Each dict typically
            contains keys like ``symbol``, ``shares``, ``market_value``,
            ``cost_basis``, etc. Serialised to JSON for storage.

    Side effects:
        - Opens and closes a SQLite connection.
        - Inserts or replaces one row in the ``portfolio_value`` table.
    """
    if isinstance(snapshot_date, str):
        snapshot_date = datetime.strptime(snapshot_date, "%Y-%m-%d").date()

    positions_json = json.dumps(positions) if positions else None

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO portfolio_value
            (date, total_value, total_cost_basis, cash, positions)
            VALUES (?, ?, ?, ?, ?)
        """,
            (
                snapshot_date.isoformat(),
                total_value,
                total_cost_basis,
                cash,
                positions_json,
            ),
        )
        conn.commit()


def get_portfolio_value_on_date(target_date: date | str) -> dict[str, Any] | None:
    """Retrieve the portfolio value snapshot for a specific date.

    If a ``positions`` column is stored as a JSON string, it is automatically
    parsed back into a Python list of dicts before returning.

    Parameters:
        target_date: The date to look up. Accepts ``datetime.date`` or
            ``"YYYY-MM-DD"`` string.

    Returns:
        A dict with columns ``date``, ``total_value``, ``total_cost_basis``,
        ``cash``, ``positions`` (parsed from JSON), and ``created_at``.
        Returns ``None`` if no snapshot exists for the given date.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    if isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d").date()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM portfolio_value
            WHERE date = ?
        """,
            (target_date.isoformat(),),
        )
        row = cursor.fetchone()

        if row:
            result = dict(row)
            if result.get("positions"):
                result["positions"] = json.loads(result["positions"])
            return result

    return None


def get_portfolio_value_history(
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve portfolio value snapshots within an optional date range.

    Returns snapshots ordered by date ascending. For each row, the
    ``positions`` column (if present) is automatically parsed from its
    JSON string representation back into a Python list of dicts.

    Parameters:
        start_date: If provided, only return snapshots on or after this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.
        end_date: If provided, only return snapshots on or before this date.
            Accepts ``datetime.date`` or ``"YYYY-MM-DD"`` string. Optional.

    Returns:
        A list of dicts, each with columns ``date``, ``total_value``,
        ``total_cost_basis``, ``cash``, ``positions`` (parsed from JSON),
        and ``created_at``. Ordered by date ascending.

    Side effects:
        - Opens and closes a SQLite connection.
        - Executes one SELECT query.
    """
    query = "SELECT * FROM portfolio_value WHERE 1=1"
    params: list[Any] = []

    if start_date:
        if isinstance(start_date, str):
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        query += " AND date >= ?"
        params.append(start_date.isoformat())

    if end_date:
        if isinstance(end_date, str):
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        query += " AND date <= ?"
        params.append(end_date.isoformat())

    query += " ORDER BY date ASC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        results: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            result = dict(row)
            if result.get("positions"):
                result["positions"] = json.loads(result["positions"])
            results.append(result)
        return results


def get_idea_performance(
    idea_id: str,
    price_at_creation: float,
    current_price: float | None = None,
) -> dict[str, Any]:
    """Calculate profit-and-loss (P&L) for an idea based on its recorded trades.

    Iterates through all trades linked to the given idea ID, computing
    total cost, total proceeds, remaining shares, realised P&L, unrealised
    P&L, and overall return percentage. If no current price is provided and
    the position is still open, attempts to fetch the latest price from the
    database.

    P&L calculation logic:
        - **Realised P&L** = total sell proceeds minus the proportional cost
          of shares sold (based on the fraction of total shares that have
          been sold).
        - **Unrealised P&L** = remaining shares * (current price - average
          cost per share).
        - **Total P&L** = realised + unrealised.
        - **Return %** = total P&L / total cost * 100.

    Parameters:
        idea_id: The idea identifier (e.g. ``"001"``).
        price_at_creation: The stock price when the idea was first created.
            Stored in the result for reference but not used in P&L math.
        current_price: The current stock price for unrealised P&L. If
            ``None`` and the position has remaining shares, the function
            looks up the latest price in the ``price_history`` table.

    Returns:
        A dict with the following keys:
            - ``idea_id`` (str): The idea ID
            - ``status`` (str): ``"no_trades"``, ``"active"`` (has remaining
              shares), or ``"closed"`` (all shares sold)
            - ``price_at_creation`` (float): As provided
            - ``current_price`` (float | None): Current price used for
              unrealised P&L
            - ``total_shares`` (float): Remaining shares held
            - ``total_cost`` (float): Total money spent on buys (incl. fees)
            - ``realized_pnl`` (float): P&L from completed sell trades
            - ``unrealized_pnl`` (float): Paper P&L on remaining shares
            - ``total_pnl`` (float): Sum of realised + unrealised
            - ``return_pct`` (float): Total return as a percentage
            - ``trades`` (list): The raw trade dicts from the database

    Side effects:
        - Opens and closes a SQLite connection (via ``get_trades_for_idea``).
        - May open another connection (via ``get_latest_price``) if
          ``current_price`` is None and the position is open.
    """
    trades = get_trades_for_idea(idea_id)

    if not trades:
        return {
            "idea_id": idea_id,
            "status": "no_trades",
            "price_at_creation": price_at_creation,
            "trades": [],
        }

    total_cost = 0.0
    total_shares = 0.0
    total_proceeds = 0.0
    total_bought = 0.0

    for trade in trades:
        if trade["action"] == "buy":
            total_cost += trade["shares"] * trade["price_per_share"] + trade.get("fees", 0)
            total_shares += trade["shares"]
            total_bought += trade["shares"]
        else:
            total_proceeds += trade["shares"] * trade["price_per_share"] - trade.get("fees", 0)
            total_shares -= trade["shares"]

    if current_price is None and total_shares > 0:
        latest = get_latest_price(trades[0]["symbol"])
        current_price = latest["close"] if latest else None

    sold_fraction = 1 - (total_shares / max(total_bought, 1))
    realized_pnl = total_proceeds - (total_cost * sold_fraction)

    unrealized_pnl = 0.0
    if total_shares > 0 and current_price and total_bought > 0:
        avg_cost = total_cost / total_bought
        unrealized_pnl = total_shares * (current_price - avg_cost)

    total_pnl = realized_pnl + unrealized_pnl
    return_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0

    return {
        "idea_id": idea_id,
        "status": "active" if total_shares > 0 else "closed",
        "price_at_creation": price_at_creation,
        "current_price": current_price,
        "total_shares": total_shares,
        "total_cost": round(total_cost, 2),
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "return_pct": round(return_pct, 2),
        "trades": trades,
    }


def calculate_what_if(
    price_at_pass: float,
    current_price: float,
) -> dict[str, Any]:
    """What-if analysis: evaluate whether passing on an idea was the right call.

    Compares the price at the time the idea was passed to the current price.
    If the price has risen more than 5%, the pass is considered a "missed
    opportunity"; otherwise it is judged as a "good pass".

    This supports the money_thoughts feedback loop: by tracking what happened
    to ideas that were passed, the system can learn whether the user's pass
    criteria are too aggressive or too conservative.

    Parameters:
        price_at_pass: The stock price when the idea was passed (from the
            idea file's ``price_at_pass`` frontmatter field).
        current_price: The current stock price for comparison.

    Returns:
        A dict with:
            - ``price_at_pass`` (float): As provided
            - ``current_price`` (float): As provided
            - ``change`` (float): Absolute price change (current - pass)
            - ``change_pct`` (float): Percentage change
            - ``pass_correct`` (bool): True if change_pct <= 5%
            - ``assessment`` (str): ``"Good pass"`` or ``"Missed opportunity"``
    """
    change = current_price - price_at_pass
    change_pct = (change / price_at_pass) * 100

    pass_correct = change_pct <= 5

    return {
        "price_at_pass": price_at_pass,
        "current_price": current_price,
        "change": round(change, 2),
        "change_pct": round(change_pct, 2),
        "pass_correct": pass_correct,
        "assessment": "Good pass" if pass_correct else "Missed opportunity",
    }


def get_next_idea_id() -> str:
    """Generate the next sequential idea ID as a zero-padded 3-digit string.

    Scans both the ``ideas/`` directory (active ideas) and the
    ``history/ideas/`` directory (archived ideas) for markdown files whose
    filenames start with a numeric prefix (e.g. ``001-AAPL-buy.md``).
    Extracts the highest numeric prefix found and returns the next integer,
    zero-padded to 3 digits.

    Returns:
        str: The next idea ID, e.g. ``"001"`` if no ideas exist, ``"042"``
        if the highest existing ID is 41.

    Side effects:
        - Reads the filesystem to scan for existing idea files.
    """
    ideas_dir = Path(__file__).parent.parent / "ideas"
    history_ideas_dir = Path(__file__).parent.parent / "history" / "ideas"

    max_id = 0

    for directory in [ideas_dir, history_ideas_dir]:
        if directory.exists():
            for f in directory.glob("*.md"):
                parts = f.stem.split("-")
                if parts and parts[0].isdigit():
                    max_id = max(max_id, int(parts[0]))

    return f"{max_id + 1:03d}"
