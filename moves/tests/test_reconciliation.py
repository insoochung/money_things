"""Tests for position reconciliation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine import Position
from engine.reconciliation import Reconciler


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.execute = MagicMock()
    db.fetch_all = MagicMock(return_value=[])
    return db


@pytest.fixture
def mock_broker():
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    return broker


@pytest.fixture
def reconciler(mock_db, mock_broker):
    return Reconciler(mock_db, mock_broker)


class TestReconcile:
    """Tests for reconciliation logic."""

    def test_matching_positions(self, reconciler, mock_db, mock_broker):
        """Matching positions are reported as matched."""
        mock_broker.get_positions.return_value = [
            Position(symbol="AAPL", shares=100, avg_cost=150.0),
        ]
        mock_db.fetch_all.return_value = [
            {"symbol": "AAPL", "shares": 100, "avg_cost": 150.0},
        ]

        result = asyncio.get_event_loop().run_until_complete(reconciler.reconcile())
        assert "AAPL" in result["matched"]
        assert len(result["discrepancies"]) == 0

    def test_share_discrepancy(self, reconciler, mock_db, mock_broker):
        """Share count mismatch is detected."""
        mock_broker.get_positions.return_value = [
            Position(symbol="AAPL", shares=100, avg_cost=150.0),
        ]
        mock_db.fetch_all.return_value = [
            {"symbol": "AAPL", "shares": 90, "avg_cost": 150.0},
        ]

        result = asyncio.get_event_loop().run_until_complete(reconciler.reconcile())
        assert len(result["discrepancies"]) == 1
        assert result["discrepancies"][0]["diff"] == 10

    def test_db_only_position(self, reconciler, mock_db, mock_broker):
        """Position in DB but not broker is flagged."""
        mock_broker.get_positions.return_value = []
        mock_db.fetch_all.return_value = [
            {"symbol": "AAPL", "shares": 100, "avg_cost": 150.0},
        ]

        result = asyncio.get_event_loop().run_until_complete(reconciler.reconcile())
        assert len(result["db_only"]) == 1

    def test_broker_only_position(self, reconciler, mock_db, mock_broker):
        """Position in broker but not DB is flagged."""
        mock_broker.get_positions.return_value = [
            Position(symbol="NVDA", shares=50, avg_cost=800.0),
        ]
        mock_db.fetch_all.return_value = []

        result = asyncio.get_event_loop().run_until_complete(reconciler.reconcile())
        assert len(result["broker_only"]) == 1


class TestAutoSync:
    """Tests for auto-sync of minor discrepancies."""

    def test_sync_minor_discrepancy(self, reconciler, mock_db):
        """Minor discrepancies (< 1 share) are synced."""
        discrepancies = [
            {"symbol": "AAPL", "db_shares": 99.5, "broker_shares": 100, "diff": 0.5},
        ]

        count = asyncio.get_event_loop().run_until_complete(reconciler.auto_sync(discrepancies))
        assert count == 1
        mock_db.execute.assert_called()

    def test_skip_major_discrepancy(self, reconciler, mock_db):
        """Major discrepancies (>= 1 share) are not synced."""
        discrepancies = [
            {"symbol": "AAPL", "db_shares": 90, "broker_shares": 100, "diff": 10},
        ]

        count = asyncio.get_event_loop().run_until_complete(reconciler.auto_sync(discrepancies))
        assert count == 0


class TestDailyCheck:
    """Tests for daily reconciliation."""

    def test_daily_check_runs(self, reconciler, mock_db, mock_broker):
        """Daily check runs reconcile and auto-sync."""
        mock_broker.get_positions.return_value = []
        mock_db.fetch_all.return_value = []

        result = asyncio.get_event_loop().run_until_complete(reconciler.daily_check())
        assert "auto_synced" in result
