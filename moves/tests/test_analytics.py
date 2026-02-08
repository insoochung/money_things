"""Tests for the AnalyticsEngine class."""

from __future__ import annotations

from db.database import Database
from engine.analytics import AnalyticsEngine


def _insert_nav_series(db: Database, values: list[tuple[str, float]]) -> None:
    """Insert a series of NAV records."""
    for date, val in values:
        db.execute(
            "INSERT INTO portfolio_value (date, total_value, cash, cost_basis) VALUES (?, ?, 0, 0)",
            (date, val),
        )
    db.connect().commit()


TradeData = tuple[float, float, int | None, float | None, str | None]


def _insert_trades(db: Database, trades: list[TradeData]) -> None:
    """Insert trades with realized_pnl. (pnl, total_value, signal_id, confidence, source)."""
    for pnl, total_val, sig_id, conf, source in trades:
        if sig_id and conf is not None:
            db.execute(
                "INSERT INTO signals (action, symbol, thesis_id, confidence, source, status) "
                "VALUES ('BUY', 'TEST', 1, ?, ?, 'executed')",
                (conf, source or "manual"),
            )
            sig_id = db.execute("SELECT last_insert_rowid()").fetchone()["last_insert_rowid()"]
        db.execute(
            "INSERT INTO trades (symbol, action, shares, price, "
            "total_value, realized_pnl, signal_id) "
            "VALUES ('TEST', 'SELL', 10, 100, ?, ?, ?)",
            (total_val, pnl, sig_id),
        )
    db.connect().commit()


class TestSharpeRatio:
    """Test Sharpe ratio calculation."""

    def test_insufficient_data(self, seeded_db: Database) -> None:
        """Sharpe with < 2 data points returns 0."""
        engine = AnalyticsEngine(seeded_db)
        assert engine.sharpe_ratio(user_id=1) == 0.0

    def test_positive_returns(self, db: Database) -> None:
        """Sharpe with consistent positive returns should be positive."""
        from datetime import UTC, datetime, timedelta

        base = datetime.now(UTC).date()
        values = [
            ((base - timedelta(days=20 - i)).isoformat(), 100000 + i * 500) for i in range(20)
        ]
        _insert_nav_series(db, values)

        engine = AnalyticsEngine(db)
        sharpe = engine.sharpe_ratio(user_id=1, days=365)
        assert sharpe > 0

    def test_flat_returns(self, db: Database) -> None:
        """Flat NAV (zero variance) returns 0 Sharpe."""
        values = [(f"2025-01-{i:02d}", 100000) for i in range(1, 11)]
        _insert_nav_series(db, values)

        engine = AnalyticsEngine(db)
        assert engine.sharpe_ratio(user_id=1, days=365) == 0.0


class TestMaxDrawdown:
    """Test drawdown detection."""

    def test_no_drawdown(self, db: Database) -> None:
        """Monotonically increasing NAV has 0 drawdown."""
        values = [(f"2025-01-{i:02d}", 100000 + i * 1000) for i in range(1, 11)]
        _insert_nav_series(db, values)

        engine = AnalyticsEngine(db)
        dd = engine.max_drawdown(user_id=1)
        assert dd["max_dd"] == 0.0

    def test_drawdown_detected(self, seeded_db: Database) -> None:
        """A peak-to-trough decline should be detected."""
        values = [
            ("2025-01-01", 100000),
            ("2025-01-02", 110000),  # peak
            ("2025-01-03", 99000),  # trough: 10% drawdown
            ("2025-01-04", 105000),
        ]
        _insert_nav_series(seeded_db, values)

        engine = AnalyticsEngine(seeded_db)
        dd = engine.max_drawdown(user_id=1)
        assert dd["max_dd"] > 0.09  # ~10%
        assert dd["peak_date"] == "2025-01-02"
        assert dd["trough_date"] == "2025-01-03"

    def test_empty_nav(self, db: Database) -> None:
        """Empty portfolio_value returns zero drawdown."""
        engine = AnalyticsEngine(db)
        dd = engine.max_drawdown(user_id=1)
        assert dd["max_dd"] == 0.0


class TestWinRate:
    """Test win rate calculations."""

    def test_no_trades(self, seeded_db: Database) -> None:
        """No trades returns 0 win rate."""
        engine = AnalyticsEngine(seeded_db)
        result = engine.win_rate(user_id=1)
        assert result["win_rate"] == 0.0

    def test_all_winners(self, seeded_db: Database) -> None:
        """All winning trades should return 100% win rate."""
        _insert_trades(
            seeded_db,
            [
                (100, 1000, 1, 0.8, "manual"),
                (200, 2000, 1, 0.7, "manual"),
            ],
        )
        engine = AnalyticsEngine(seeded_db)
        result = engine.win_rate(user_id=1)
        assert result["win_rate"] == 1.0
        assert result["wins"] == 2

    def test_mixed_results(self, seeded_db: Database) -> None:
        """Mix of wins and losses should compute correctly."""
        _insert_trades(
            seeded_db,
            [
                (100, 1000, 1, 0.8, "manual"),
                (-50, 500, 1, 0.6, "manual"),
            ],
        )
        engine = AnalyticsEngine(seeded_db)
        result = engine.win_rate(user_id=1)
        assert result["win_rate"] == 0.5

    def test_group_by_source(self, seeded_db: Database) -> None:
        """Grouping by source should produce per-source stats."""
        _insert_trades(
            seeded_db,
            [
                (100, 1000, 1, 0.8, "manual"),
                (-50, 500, 1, 0.6, "thesis_update"),
            ],
        )
        engine = AnalyticsEngine(seeded_db)
        result = engine.win_rate(user_id=1, group_by="source")
        assert "manual" in result
        assert "thesis_update" in result


class TestStressTest:
    """Test stress test calculations."""

    def test_stress_with_nav(self, seeded_db: Database) -> None:
        """Stress test with existing NAV should return estimated loss."""
        engine = AnalyticsEngine(seeded_db)
        result = engine.stress_test(user_id=1, market_drop=-0.20)
        assert result["current_nav"] == 100000
        assert result["estimated_nav"] <= 100000
        assert "Market" in result["scenario"]

    def test_stress_empty(self, db: Database) -> None:
        """Stress test with no NAV returns zero impact."""
        engine = AnalyticsEngine(db)
        result = engine.stress_test(user_id=1)
        assert result["current_nav"] == 0.0


class TestVaR:
    """Test Value-at-Risk calculation."""

    def test_var_insufficient_data(self, seeded_db: Database) -> None:
        """VaR with insufficient data returns 0."""
        engine = AnalyticsEngine(seeded_db)
        assert engine.var_95(user_id=1) == 0.0


class TestNavSnapshot:
    """Test NAV snapshot creation."""

    def test_snapshot_creates_record(self, seeded_db: Database) -> None:
        """snapshot_nav should insert a portfolio_value row."""
        engine = AnalyticsEngine(seeded_db)
        initial_count = len(seeded_db.fetchall("SELECT * FROM portfolio_value"))
        engine.snapshot_nav(user_id=1)
        new_count = len(seeded_db.fetchall("SELECT * FROM portfolio_value"))
        assert new_count == initial_count + 1

    def test_snapshot_exposure(self, seeded_db: Database) -> None:
        """snapshot_exposure should insert an exposure_snapshots row."""
        engine = AnalyticsEngine(seeded_db)
        engine.snapshot_exposure(user_id=1)
        rows = seeded_db.fetchall("SELECT * FROM exposure_snapshots")
        assert len(rows) == 1


class TestCalibration:
    """Test calibration analysis."""

    def test_empty_calibration(self, seeded_db: Database) -> None:
        """No trades returns empty calibration."""
        engine = AnalyticsEngine(seeded_db)
        assert engine.calibration(user_id=1) == []


class TestNavHistory:
    """Test NAV history retrieval."""

    def test_returns_existing_records(self, seeded_db: Database) -> None:
        """nav_history should return seeded portfolio_value records."""
        engine = AnalyticsEngine(seeded_db)
        history = engine.nav_history(user_id=1)
        assert len(history) >= 1
        assert "date" in history[0]
        assert "nav" in history[0]
