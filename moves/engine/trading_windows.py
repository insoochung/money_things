"""Trading window enforcement for restricted trading periods.

Manages time-based trading restrictions (e.g., META employee trading windows,
earnings blackout periods). Checks whether trading is currently allowed for
a given symbol and provides countdown information for the dashboard.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.database import Database

logger = logging.getLogger(__name__)


class TradingWindowManager:
    """Enforce trading windows for restricted symbols.

    Queries the trading_windows table to determine if a symbol is currently
    in an open trading window. If no windows are defined for a symbol,
    trading is always allowed.

    Attributes:
        db: Database instance for querying trading windows.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the trading window manager.

        Args:
            db: Database instance.
        """
        self.db = db

    def is_allowed(self, symbol: str) -> bool:
        """Check if trading is currently allowed for a symbol.

        A symbol is allowed to trade if:
        1. No trading windows are defined for it (unrestricted), OR
        2. The current time falls within at least one open window.

        Args:
            symbol: Ticker symbol to check.

        Returns:
            True if trading is currently allowed.
        """
        windows = self.db.fetch_all(
            "SELECT open_date, close_date FROM trading_windows WHERE symbol = ?",
            (symbol,),
        )
        if not windows:
            return True

        now = datetime.now(UTC).isoformat()
        return any(w["open_date"] <= now <= w["close_date"] for w in windows)

    def get_windows(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get trading windows, optionally filtered by symbol.

        Args:
            symbol: Filter by symbol, or None for all windows.

        Returns:
            List of trading window dictionaries.
        """
        if symbol:
            return self.db.fetch_all(
                "SELECT * FROM trading_windows WHERE symbol = ? ORDER BY open_date",
                (symbol,),
            )
        return self.db.fetch_all("SELECT * FROM trading_windows ORDER BY symbol, open_date")

    def next_window_close(self, symbol: str) -> dict[str, Any] | None:
        """Find the next closing time for a currently open window.

        Used by the dashboard to show a countdown timer.

        Args:
            symbol: Ticker symbol to check.

        Returns:
            Dictionary with 'close_date' and 'reason', or None if no open window.
        """
        now = datetime.now(UTC).isoformat()
        row = self.db.fetch_one(
            """SELECT close_date, reason FROM trading_windows
               WHERE symbol = ? AND open_date <= ? AND close_date >= ?
               ORDER BY close_date ASC LIMIT 1""",
            (symbol, now, now),
        )
        if not row:
            return None

        close_dt = datetime.fromisoformat(row["close_date"])
        now_dt = datetime.now(UTC)
        remaining = close_dt - now_dt
        return {
            "close_date": row["close_date"],
            "reason": row["reason"],
            "remaining_seconds": max(0, int(remaining.total_seconds())),
        }

    def add_window(
        self,
        symbol: str,
        open_date: str,
        close_date: str,
        reason: str = "",
    ) -> int:
        """Add a new trading window.

        Args:
            symbol: Ticker symbol.
            open_date: ISO 8601 start of window.
            close_date: ISO 8601 end of window.
            reason: Description of why this window exists.

        Returns:
            Row ID of the created window.
        """
        cursor = self.db.execute(
            """INSERT INTO trading_windows (symbol, open_date, close_date, reason)
               VALUES (?, ?, ?, ?)""",
            (symbol, open_date, close_date, reason),
        )
        return cursor.lastrowid  # type: ignore[return-value]
