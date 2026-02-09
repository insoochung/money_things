"""Tests for the outcome tracker module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from engine.outcome_tracker import (
    OutcomeTracker,
    SymbolReturn,
    ThesisScorecard,
    _compute_calibration,
    _days_to_period,
)


@pytest.fixture
def mock_db(tmp_path):
    """Create a minimal moves DB with test data."""
    from db.database import Database

    db_path = tmp_path / "moves.db"
    db = Database(str(db_path))

    # Create tables
    db.execute("""
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            thesis_text TEXT DEFAULT '',
            strategy TEXT DEFAULT 'long',
            status TEXT DEFAULT 'active',
            symbols TEXT DEFAULT '[]',
            universe_keywords TEXT DEFAULT '[]',
            validation_criteria TEXT DEFAULT '[]',
            failure_criteria TEXT DEFAULT '[]',
            horizon TEXT DEFAULT '',
            conviction REAL DEFAULT 0.5,
            source_module TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            user_id INTEGER DEFAULT 1
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            interval TEXT DEFAULT '1d',
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume INTEGER,
            UNIQUE(symbol, timestamp, interval)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS outcome_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id INTEGER NOT NULL,
            snapshot_date TEXT NOT NULL,
            symbols TEXT NOT NULL,
            conviction REAL NOT NULL,
            avg_return_pct REAL,
            best_symbol TEXT,
            best_return_pct REAL,
            worst_symbol TEXT,
            worst_return_pct REAL,
            thesis_age_days INTEGER,
            calibration_score REAL,
            details_json TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(thesis_id, snapshot_date)
        )
    """)

    # Insert test thesis
    created = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    db.execute(
        """INSERT INTO theses (id, title, status, symbols, conviction, created_at)
           VALUES (1, 'META AI thesis', 'active', 'META', 85, ?)""",
        (created,),
    )
    db.execute(
        """INSERT INTO theses (id, title, status, symbols, conviction, created_at)
           VALUES (2, 'AI infra', 'draft', 'AVGO,MRVL', 55, ?)""",
        (created,),
    )

    # Insert price history
    db.execute(
        """INSERT INTO price_history (symbol, timestamp, close, volume)
           VALUES ('META', ?, 500.00, 1000000)""",
        (created[:10],),
    )
    db.connect().commit()
    return db


class TestComputeCalibration:
    def test_high_conviction_positive_return(self):
        # 85% conviction, +20% return → good calibration
        score = _compute_calibration(85, 20)
        assert score > 60

    def test_high_conviction_negative_return(self):
        # 85% conviction, -20% return → bad calibration
        score = _compute_calibration(85, -20)
        assert score < 40

    def test_low_conviction_flat_return(self):
        # 50% conviction, 0% return → neutral
        score = _compute_calibration(50, 0)
        assert score == 50

    def test_score_bounded(self):
        assert 0 <= _compute_calibration(100, 100) <= 100
        assert 0 <= _compute_calibration(0, -100) <= 100
        assert 0 <= _compute_calibration(100, -100) <= 100


class TestDaysToPeriod:
    def test_short(self):
        assert _days_to_period(3) == "5d"

    def test_month(self):
        assert _days_to_period(20) == "1mo"

    def test_quarter(self):
        assert _days_to_period(60) == "3mo"

    def test_year(self):
        assert _days_to_period(300) == "1y"

    def test_multi_year(self):
        assert _days_to_period(500) == "2y"
        assert _days_to_period(1000) == "5y"


class TestThesisScorecard:
    def test_to_dict(self):
        sc = ThesisScorecard(
            thesis_id=1, title="Test", conviction=85,
            status="active", symbols=["META"], created_at="2025-01-01",
            avg_return_pct=15.5, calibration_score=72.0,
        )
        d = sc.to_dict()
        assert d["thesis_id"] == 1
        assert d["avg_return_pct"] == 15.5

    def test_format_telegram(self):
        sc = ThesisScorecard(
            thesis_id=1, title="META thesis", conviction=85,
            status="active", symbols=["META"], created_at="2025-01-01",
            age_days=30, avg_return_pct=10.5, calibration_score=70.0,
            symbol_returns=[
                SymbolReturn(symbol="META", current_price=550, return_pct=10.5, period_days=30),
            ],
        )
        msg = sc.format_telegram()
        assert "META thesis" in msg
        assert "+10.5%" in msg
        assert "Calibration" in msg

    def test_format_telegram_with_error(self):
        sc = ThesisScorecard(
            thesis_id=1, title="Test", conviction=50,
            status="draft", symbols=["XYZ"], created_at="2025-01-01",
            symbol_returns=[
                SymbolReturn(symbol="XYZ", error="Price unavailable"),
            ],
        )
        msg = sc.format_telegram()
        assert "Price unavailable" in msg


class TestOutcomeTracker:
    def test_score_thesis_no_prices(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        sc = tracker.score_thesis(1, fetch_prices=False)
        assert sc is not None
        assert sc.thesis_id == 1
        assert sc.title == "META AI thesis"
        assert sc.conviction == 85
        assert sc.symbol_returns == []

    def test_score_thesis_not_found(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        assert tracker.score_thesis(999, fetch_prices=False) is None

    def test_score_all_no_prices(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        scorecards = tracker.score_all(fetch_prices=False)
        assert len(scorecards) == 2
        assert scorecards[0].thesis_id == 1
        assert scorecards[1].thesis_id == 2

    @patch("engine.outcome_tracker.get_price")
    @patch("engine.outcome_tracker.get_history")
    def test_score_thesis_with_prices(self, mock_hist, mock_price, mock_db):
        mock_price.return_value = {"symbol": "META", "price": 550.0}
        mock_hist.return_value = []  # force DB fallback

        tracker = OutcomeTracker(mock_db)
        sc = tracker.score_thesis(1, fetch_prices=True)

        assert sc is not None
        assert len(sc.symbol_returns) == 1
        sr = sc.symbol_returns[0]
        assert sr.symbol == "META"
        assert sr.current_price == 550.0
        # Should have found price_history entry at 500
        assert sr.price_at_thesis_creation == 500.0
        assert sr.return_pct == 10.0  # (550-500)/500 * 100

        assert sc.avg_return_pct == 10.0
        assert sc.calibration_score is not None

    def test_persist_snapshot(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        sc = ThesisScorecard(
            thesis_id=1, title="META", conviction=85,
            status="active", symbols=["META"], created_at="2025-01-01",
            age_days=30, avg_return_pct=10.0, calibration_score=70.0,
            best_symbol="META", best_return_pct=10.0,
            worst_symbol="META", worst_return_pct=10.0,
        )
        tracker.persist_snapshot(sc)

        rows = mock_db.execute(
            "SELECT * FROM outcome_snapshots WHERE thesis_id = 1"
        ).fetchall()
        assert len(rows) == 1

    def test_get_history(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        # Persist two snapshots
        sc = ThesisScorecard(
            thesis_id=1, title="META", conviction=85,
            status="active", symbols=["META"], created_at="2025-01-01",
        )
        tracker.persist_snapshot(sc)

        history = tracker.get_history(1)
        assert len(history) == 1

    def test_format_summary(self, mock_db):
        tracker = OutcomeTracker(mock_db)
        scorecards = [
            ThesisScorecard(
                thesis_id=1, title="META", conviction=85,
                status="active", symbols=["META"], created_at="2025-01-01",
                avg_return_pct=10.0, calibration_score=70.0, age_days=30,
                symbol_returns=[
                    SymbolReturn(symbol="META", return_pct=10.0, period_days=30),
                ],
            ),
        ]
        msg = tracker.format_summary(scorecards)
        assert "Outcome Report" in msg
        assert "META" in msg
        assert "Portfolio avg" in msg
