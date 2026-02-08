"""Tests for trading window enforcement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from engine.trading_windows import TradingWindowManager


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = MagicMock()
    db.fetch_all = MagicMock(return_value=[])
    db.fetch_one = MagicMock(return_value=None)
    return db


@pytest.fixture
def manager(mock_db):
    return TradingWindowManager(mock_db)


class TestIsAllowed:
    """Tests for trading permission checks."""

    def test_no_windows_allows_trading(self, manager, mock_db):
        """Symbol with no windows is always allowed."""
        mock_db.fetch_all.return_value = []
        assert manager.is_allowed("AAPL") is True

    def test_within_open_window(self, manager, mock_db):
        """Trading allowed when inside an open window."""
        now = datetime.now(UTC)
        mock_db.fetch_all.return_value = [
            {
                "open_date": (now - timedelta(days=1)).isoformat(),
                "close_date": (now + timedelta(days=1)).isoformat(),
            }
        ]
        assert manager.is_allowed("META") is True

    def test_outside_window(self, manager, mock_db):
        """Trading blocked when outside all windows."""
        now = datetime.now(UTC)
        mock_db.fetch_all.return_value = [
            {
                "open_date": (now + timedelta(days=10)).isoformat(),
                "close_date": (now + timedelta(days=20)).isoformat(),
            }
        ]
        assert manager.is_allowed("META") is False


class TestGetWindows:
    """Tests for window retrieval."""

    def test_get_all_windows(self, manager, mock_db):
        """Get all windows when no symbol specified."""
        mock_db.fetch_all.return_value = [
            {"id": 1, "symbol": "META", "open_date": "2026-01-01", "close_date": "2026-02-01"},
        ]
        windows = manager.get_windows()
        assert len(windows) == 1

    def test_get_windows_by_symbol(self, manager, mock_db):
        """Filter windows by symbol."""
        mock_db.fetch_all.return_value = [
            {"id": 1, "symbol": "META", "open_date": "2026-01-01", "close_date": "2026-02-01"},
        ]
        windows = manager.get_windows("META")
        assert len(windows) == 1


class TestNextWindowClose:
    """Tests for countdown functionality."""

    def test_no_open_window(self, manager, mock_db):
        """Returns None when no window is currently open."""
        mock_db.fetch_one.return_value = None
        assert manager.next_window_close("META") is None

    def test_open_window_countdown(self, manager, mock_db):
        """Returns remaining seconds for open window."""
        close_time = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        mock_db.fetch_one.return_value = {
            "close_date": close_time,
            "reason": "Q4 earnings",
        }

        result = manager.next_window_close("META")
        assert result is not None
        assert result["remaining_seconds"] > 0
        assert result["reason"] == "Q4 earnings"


class TestAddWindow:
    """Tests for adding windows."""

    def test_add_window(self, manager, mock_db):
        """Window is inserted into database."""
        cursor = MagicMock()
        cursor.lastrowid = 1
        mock_db.execute.return_value = cursor

        row_id = manager.add_window("META", "2026-03-01", "2026-03-15", "Quiet period")
        assert row_id == 1
        mock_db.execute.assert_called_once()
