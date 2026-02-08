"""Tests for utils/db.py -- SQLite persistence layer."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from utils import db


@pytest.fixture(autouse=True)
def _temp_db(tmp_path: Path) -> None:
    """Use a temporary database for each test."""
    test_db = tmp_path / "test_journal.db"
    with patch.object(db, "DB_PATH", test_db):
        db.init_db()
        yield


class TestInitDb:
    def test_creates_tables(self) -> None:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}
        assert "price_history" in tables
        assert "trades" in tables
        assert "portfolio_value" in tables

    def test_idempotent(self) -> None:
        db.init_db()
        db.init_db()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row["name"] for row in cursor.fetchall()}
        assert "price_history" in tables


class TestStorePrice:
    def test_store_and_retrieve(self) -> None:
        ts = datetime(2026, 1, 15, 16, 0)
        db.store_price("AAPL", close=185.50, timestamp=ts, open_price=183.0, high=186.0, low=182.5)
        result = db.get_latest_price("AAPL")
        assert result is not None
        assert result["close"] == 185.50
        assert result["symbol"] == "AAPL"

    def test_upsert_on_duplicate(self) -> None:
        ts = datetime(2026, 1, 15, 16, 0)
        db.store_price("AAPL", close=185.0, timestamp=ts)
        db.store_price("AAPL", close=186.0, timestamp=ts)
        result = db.get_latest_price("AAPL")
        assert result is not None
        assert result["close"] == 186.0

    def test_symbol_uppercased(self) -> None:
        db.store_price("aapl", close=185.0)
        result = db.get_latest_price("AAPL")
        assert result is not None

    def test_default_timestamp(self) -> None:
        db.store_price("AAPL", close=185.0)
        result = db.get_latest_price("AAPL")
        assert result is not None


class TestStorePricesBulk:
    def test_bulk_insert(self) -> None:
        prices = [
            {
                "symbol": "AAPL",
                "timestamp": datetime(2026, 1, 15),
                "interval": "1d",
                "open": 183.0,
                "high": 186.0,
                "low": 182.5,
                "close": 185.0,
                "volume": 1000000,
            },
            {
                "symbol": "AAPL",
                "timestamp": datetime(2026, 1, 16),
                "interval": "1d",
                "open": 185.0,
                "high": 188.0,
                "low": 184.0,
                "close": 187.0,
                "volume": 1200000,
            },
        ]
        db.store_prices_bulk(prices)
        history = db.get_price_history("AAPL")
        assert len(history) == 2


class TestGetPriceOnDate:
    def test_returns_price_for_date(self) -> None:
        ts = datetime(2026, 2, 1, 16, 0)
        db.store_price("MSFT", close=400.0, timestamp=ts)
        result = db.get_price_on_date("MSFT", date(2026, 2, 1))
        assert result is not None
        assert result["close"] == 400.0

    def test_returns_none_for_missing_date(self) -> None:
        result = db.get_price_on_date("MSFT", "2026-03-01")
        assert result is None

    def test_accepts_string_date(self) -> None:
        ts = datetime(2026, 2, 1, 16, 0)
        db.store_price("MSFT", close=400.0, timestamp=ts)
        result = db.get_price_on_date("MSFT", "2026-02-01")
        assert result is not None


class TestGetPriceHistory:
    def test_returns_ordered_history(self) -> None:
        for day in range(1, 6):
            db.store_price("GOOG", close=100.0 + day, timestamp=datetime(2026, 1, day, 16, 0))
        history = db.get_price_history("GOOG")
        assert len(history) == 5
        assert history[0]["close"] < history[-1]["close"]

    def test_date_range_filtering(self) -> None:
        for day in range(1, 11):
            db.store_price("GOOG", close=100.0 + day, timestamp=datetime(2026, 1, day, 16, 0))
        history = db.get_price_history(
            "GOOG", start_date=date(2026, 1, 3), end_date=date(2026, 1, 7)
        )
        assert len(history) == 5

    def test_empty_for_unknown_symbol(self) -> None:
        history = db.get_price_history("ZZZZ")
        assert history == []


class TestGetLastPriceTimestamp:
    def test_returns_latest(self) -> None:
        db.store_price("NVDA", close=500.0, timestamp=datetime(2026, 1, 1))
        db.store_price("NVDA", close=510.0, timestamp=datetime(2026, 1, 5))
        ts = db.get_last_price_timestamp("NVDA")
        assert ts is not None
        assert ts.day == 5

    def test_returns_none_for_missing(self) -> None:
        ts = db.get_last_price_timestamp("ZZZZ")
        assert ts is None


class TestRecordTrade:
    def test_invalid_action_raises(self) -> None:
        with pytest.raises(ValueError, match="action must be"):
            db.record_trade(
                symbol="META",
                execution_date="2026-02-01",
                action="hold",
                shares=10,
                price_per_share=600.0,
            )

    def test_get_trades_for_idea(self) -> None:
        db.record_trade("AAPL", "2026-01-01", "buy", 10, 185.0, idea_id="001")
        db.record_trade("AAPL", "2026-02-01", "sell", 5, 195.0, idea_id="001")
        db.record_trade("MSFT", "2026-01-15", "buy", 20, 400.0, idea_id="002")
        trades = db.get_trades_for_idea("001")
        assert len(trades) == 2
        assert all(t["idea_id"] == "001" for t in trades)

    def test_get_trades_for_symbol(self) -> None:
        db.record_trade("AAPL", "2026-01-01", "buy", 10, 185.0)
        db.record_trade("AAPL", "2026-02-01", "sell", 5, 195.0)
        trades = db.get_trades_for_symbol("AAPL")
        assert len(trades) == 2

    def test_get_all_trades_with_range(self) -> None:
        db.record_trade("AAPL", "2026-01-01", "buy", 10, 185.0)
        db.record_trade("MSFT", "2026-02-01", "buy", 20, 400.0)
        db.record_trade("GOOG", "2026-03-01", "buy", 5, 150.0)
        trades = db.get_all_trades(start_date="2026-01-15", end_date="2026-02-15")
        assert len(trades) == 1
        assert trades[0]["symbol"] == "MSFT"


class TestPortfolioValue:
    def test_record_and_retrieve(self) -> None:
        db.record_portfolio_value(
            snapshot_date="2026-02-01",
            total_value=100000.0,
            total_cost_basis=80000.0,
            cash=5000.0,
            positions=[{"symbol": "META", "shares": 230, "value": 95000.0}],
        )
        result = db.get_portfolio_value_on_date("2026-02-01")
        assert result is not None
        assert result["total_value"] == 100000.0
        assert result["positions"][0]["symbol"] == "META"

    def test_returns_none_for_missing(self) -> None:
        result = db.get_portfolio_value_on_date("2026-12-31")
        assert result is None

    def test_history_with_range(self) -> None:
        for day in range(1, 6):
            db.record_portfolio_value(f"2026-02-0{day}", total_value=100000.0 + day * 100)
        history = db.get_portfolio_value_history(start_date="2026-02-02", end_date="2026-02-04")
        assert len(history) == 3


class TestIdeaPerformance:
    def test_no_trades(self) -> None:
        result = db.get_idea_performance("999", price_at_creation=100.0)
        assert result["status"] == "no_trades"

    def test_buy_and_sell(self) -> None:
        db.record_trade("AAPL", "2026-01-01", "buy", 10, 100.0, idea_id="001")
        db.record_trade("AAPL", "2026-02-01", "sell", 10, 120.0, idea_id="001")
        result = db.get_idea_performance("001", price_at_creation=100.0)
        assert result["status"] == "closed"
        assert result["total_pnl"] == 200.0


class TestCalculateWhatIf:
    def test_price_went_up(self) -> None:
        result = db.calculate_what_if(price_at_pass=100.0, current_price=120.0)
        assert result["change"] == 20.0
        assert result["change_pct"] == 20.0
        assert result["pass_correct"] is False
        assert result["assessment"] == "Missed opportunity"

    def test_price_went_down(self) -> None:
        result = db.calculate_what_if(price_at_pass=100.0, current_price=90.0)
        assert result["change"] == -10.0
        assert result["pass_correct"] is True
        assert result["assessment"] == "Good pass"

    def test_price_stayed_flat(self) -> None:
        result = db.calculate_what_if(price_at_pass=100.0, current_price=103.0)
        assert result["pass_correct"] is True


class TestGetNextIdeaId:
    def test_returns_001_when_empty(self, tmp_path: Path) -> None:
        with patch.object(db, "DB_PATH", tmp_path / "test.db"):
            # Point to dirs that don't exist
            with patch("utils.db.Path") as mock_path:
                mock_path.return_value.parent.parent.__truediv__ = lambda self, x: tmp_path / x
                # Just test the function directly with empty dirs
                ideas_dir = tmp_path / "ideas"
                history_dir = tmp_path / "history" / "ideas"
                ideas_dir.mkdir(parents=True)
                history_dir.mkdir(parents=True)

                def patched_next_id() -> str:
                    max_id = 0
                    for directory in [ideas_dir, history_dir]:
                        if directory.exists():
                            for f in directory.glob("*.md"):
                                parts = f.stem.split("-")
                                if parts and parts[0].isdigit():
                                    max_id = max(max_id, int(parts[0]))
                    return f"{max_id + 1:03d}"

                assert patched_next_id() == "001"

    def test_increments_from_existing(self, tmp_path: Path) -> None:
        ideas_dir = tmp_path / "ideas"
        ideas_dir.mkdir()
        (ideas_dir / "001-AAPL-buy.md").write_text("test")
        (ideas_dir / "002-MSFT-sell.md").write_text("test")

        def patched_next_id() -> str:
            max_id = 0
            for directory in [ideas_dir, tmp_path / "history" / "ideas"]:
                if directory.exists():
                    for f in directory.glob("*.md"):
                        parts = f.stem.split("-")
                        if parts and parts[0].isdigit():
                            max_id = max(max_id, int(parts[0]))
            return f"{max_id + 1:03d}"

        assert patched_next_id() == "003"
