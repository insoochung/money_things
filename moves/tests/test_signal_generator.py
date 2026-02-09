"""Tests for the signal generation pipeline with multi-factor scoring."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from engine import SignalStatus, ThesisStatus
from engine.earnings_calendar import is_earnings_imminent
from engine.risk import RiskManager
from engine.signal_generator import (
    _BASE_POSITION_SIZE,
    _MIN_THINK_SESSIONS,
    MultiFactorScore,
    SignalGenerator,
)
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine


def _make_pricing(change_pct: float = 2.4, price: float = 150.0):
    """Create a fake pricing module with configurable values."""
    return SimpleNamespace(
        get_price=lambda symbol, db=None: {
            "symbol": symbol,
            "price": price,
            "change": price * change_pct / 100,
            "change_percent": change_pct,
            "volume": 1000000,
            "timestamp": "2026-02-08T12:00:00",
            "source": "yfinance",
        },
        get_history=lambda symbol, period="5d", db=None: [
            {"date": "2026-02-03", "close": 140.0, "open": 139.0,
             "high": 141.0, "low": 138.0, "volume": 500000},
            {"date": "2026-02-07", "close": 150.0, "open": 148.0,
             "high": 151.0, "low": 147.5, "volume": 540000},
        ],
    )


def _make_generator(db, pricing=None):
    """Create a SignalGenerator with optional custom pricing."""
    return SignalGenerator(
        db=db,
        signal_engine=SignalEngine(db=db),
        thesis_engine=ThesisEngine(db=db),
        risk_manager=RiskManager(db=db),
        pricing=pricing or _make_pricing(),
    )


def _seed_mature_thesis(db, conviction=0.8, days_old=14):
    """Seed a thesis that passes maturity gates.

    Updates the seeded thesis to be old enough and have enough versions.
    """
    created = (
        datetime.now(UTC) - timedelta(days=days_old)
    ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE theses SET created_at = ?, conviction = ? WHERE id = 1",
        (created, conviction),
    )
    # Add thesis_versions to simulate /think sessions
    for i in range(_MIN_THINK_SESSIONS):
        db.execute(
            """INSERT INTO thesis_versions
               (thesis_id, new_status, reason)
               VALUES (1, 'active', ?)""",
            (f"Think session {i + 1}",),
        )
    db.connect().commit()


@pytest.fixture
def generator(seeded_db):
    """Create a SignalGenerator with a mature thesis and mock pricing."""
    _seed_mature_thesis(seeded_db)
    return _make_generator(seeded_db)


# --- Basic signal generation ---


def test_run_scan_generates_buy_for_unheld_symbols(generator):
    """Active thesis with unheld symbols generates BUY signals."""
    results = generator.run_scan()
    assert len(results) >= 1
    for r in results:
        assert r["action"] == "BUY"
        assert r["symbol"] in ("NVDA", "AVGO")
        assert r["confidence"] > 0
        assert r["signal_id"] is not None


def test_run_scan_no_duplicates(generator):
    """Should not create duplicate signals for symbols with pending signals."""
    first = generator.run_scan()
    assert len(first) >= 1
    second = generator.run_scan()
    assert len(second) == 0


def test_run_scan_generates_sell_for_weakening_thesis(seeded_db):
    """Weakening thesis generates SELL signals for held positions."""
    _seed_mature_thesis(seeded_db)
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.WEAKENING, reason="Test")

    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 10, 140.0, 'long')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db, _make_pricing(0.3))
    results = sg.run_scan()
    sells = [r for r in results if r["action"] == "SELL"]
    assert len(sells) >= 1
    assert sells[0]["symbol"] == "NVDA"


def test_run_scan_skips_archived_theses(seeded_db):
    """Archived theses generate no signals."""
    _seed_mature_thesis(seeded_db)
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.ARCHIVED, reason="Done")

    sg = _make_generator(seeded_db, _make_pricing(5.0))
    results = sg.run_scan()
    assert len(results) == 0


def test_compute_position_size(generator):
    """Position size scales with confidence and caps at max."""
    size = generator._compute_position_size("NVDA", 0.5, 100000)
    expected = _BASE_POSITION_SIZE * 0.5 * 2
    assert size == pytest.approx(expected)

    size_high = generator._compute_position_size("NVDA", 1.0, 100000)
    assert size_high == pytest.approx(_BASE_POSITION_SIZE * 1.0 * 2)

    size_zero = generator._compute_position_size("NVDA", 0.5, 0)
    assert size_zero == _BASE_POSITION_SIZE


def test_signals_persisted_to_db(generator, seeded_db):
    """Generated signals are persisted in the database."""
    results = generator.run_scan()
    assert len(results) >= 1
    for r in results:
        signal = generator.signal_engine.get_signal(r["signal_id"])
        assert signal is not None
        assert signal.status == SignalStatus.PENDING
        assert signal.symbol == r["symbol"]


def test_no_signal_below_confidence_threshold(seeded_db):
    """Very low scored confidence signals are skipped."""
    _seed_mature_thesis(seeded_db)
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.WEAKENING, reason="weak")
    te.transition_status(
        1, ThesisStatus.INVALIDATED, reason="invalid",
    )

    sg = _make_generator(seeded_db, _make_pricing(3.0))
    results = sg.run_scan()
    assert len(results) == 0


# --- Multi-factor scoring ---


def test_multi_factor_score_all_neutral(seeded_db):
    """Neutral factors produce a moderate weighted score."""
    _seed_mature_thesis(seeded_db)
    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    score = sg._compute_multi_factor_score("NVDA", thesis, None)
    assert isinstance(score, MultiFactorScore)
    assert 0.0 < score.weighted_total < 1.0
    assert score.thesis_conviction == 0.8


def test_multi_factor_score_with_watchlist_trigger(seeded_db):
    """Watchlist trigger hit boosts the score."""
    _seed_mature_thesis(seeded_db)
    # Create the watchlist_triggers table
    seeded_db.execute(
        """CREATE TABLE IF NOT EXISTS watchlist_triggers (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               thesis_id INTEGER,
               symbol TEXT NOT NULL,
               trigger_type TEXT NOT NULL,
               condition TEXT NOT NULL,
               target_value REAL NOT NULL,
               active INTEGER DEFAULT 1,
               created_at TEXT DEFAULT CURRENT_TIMESTAMP,
               triggered_at TEXT
           )""",
    )
    seeded_db.execute(
        """INSERT INTO watchlist_triggers
           (thesis_id, symbol, trigger_type, condition,
            target_value, active, triggered_at)
           VALUES (1, 'NVDA', 'entry', 'price_below', 145.0,
                   1, '2026-02-08')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    score = sg._compute_multi_factor_score("NVDA", thesis, None)
    assert score.watchlist_trigger == 1.0

    # Score without trigger for comparison
    score_no = sg._compute_multi_factor_score("AVGO", thesis, None)
    assert score_no.watchlist_trigger == 0.0
    assert score.weighted_total > score_no.weighted_total


def test_multi_factor_score_with_news_sentiment(seeded_db):
    """News sentiment affects the score."""
    _seed_mature_thesis(seeded_db)
    # Insert supporting news
    for i in range(3):
        seeded_db.execute(
            """INSERT INTO thesis_news
               (thesis_id, headline, sentiment, timestamp)
               VALUES (1, ?, 'supporting', datetime('now'))""",
            (f"Good news {i}",),
        )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    score = sg._compute_multi_factor_score("NVDA", thesis, None)
    assert score.news_sentiment > 0.5


def test_multi_factor_score_congress_alignment(seeded_db):
    """Congress trades affect the alignment score."""
    _seed_mature_thesis(seeded_db)
    seeded_db.execute(
        """INSERT INTO congress_trades
           (politician, symbol, action, date_traded)
           VALUES ('Test Senator', 'NVDA', 'buy', date('now'))""",
    )
    seeded_db.execute(
        """INSERT INTO politician_scores
           (politician, score, tier)
           VALUES ('Test Senator', 85, 'whale')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    score = sg._compute_multi_factor_score("NVDA", thesis, None)
    assert score.congress_alignment > 0.5


# --- Blocking conditions ---


def test_blocks_low_conviction(seeded_db):
    """Signals blocked when conviction < 70%."""
    _seed_mature_thesis(seeded_db, conviction=0.5)
    sg = _make_generator(seeded_db)
    results = sg.run_scan()
    assert len(results) == 0


def test_blocks_young_thesis(seeded_db):
    """Signals blocked when thesis < 1 week old."""
    _seed_mature_thesis(seeded_db, days_old=3)
    sg = _make_generator(seeded_db)
    results = sg.run_scan()
    assert len(results) == 0


def test_blocks_insufficient_think_sessions(seeded_db):
    """Signals blocked when < 2 /think sessions."""
    created = (
        datetime.now(UTC) - timedelta(days=14)
    ).strftime("%Y-%m-%d %H:%M:%S")
    seeded_db.execute(
        "UPDATE theses SET created_at = ?, conviction = 0.8 WHERE id = 1",
        (created,),
    )
    seeded_db.connect().commit()
    # No thesis_versions inserted → 0 sessions

    sg = _make_generator(seeded_db)
    results = sg.run_scan()
    assert len(results) == 0


def test_blocks_earnings_imminent(seeded_db):
    """Signals blocked when earnings are within 5 days."""
    _seed_mature_thesis(seeded_db)

    # Create a temp earnings calendar
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"NVDA": [tomorrow]}, f)
        config_path = f.name

    assert is_earnings_imminent("NVDA", config_path=config_path)
    assert not is_earnings_imminent("AVGO", config_path=config_path)


def test_blocks_trading_window_blackout(seeded_db):
    """Signals blocked when symbol is in trading window blackout."""
    _seed_mature_thesis(seeded_db)

    # Set up a past trading window (not currently open)
    seeded_db.execute(
        """INSERT INTO trading_windows (symbol, opens, closes, notes)
           VALUES ('NVDA', '2025-01-01', '2025-01-15', 'Past window')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db)
    # NVDA has a window defined but it's not open → blacked out
    assert sg._is_in_trading_blackout("NVDA") is True
    # AVGO has no windows → not blacked out
    assert sg._is_in_trading_blackout("AVGO") is False


def test_sell_signals_bypass_blocking(seeded_db):
    """SELL signals are not blocked by maturity gates."""
    # Young thesis, low conviction — but weakening with held position
    _seed_mature_thesis(seeded_db, conviction=0.5, days_old=3)
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.WEAKENING, reason="Test")

    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 10, 140.0, 'long')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db, _make_pricing(0.3))
    results = sg.run_scan()
    sells = [r for r in results if r["action"] == "SELL"]
    assert len(sells) >= 1


# --- Earnings calendar module ---


def test_earnings_imminent_no_config():
    """No config file → not imminent."""
    assert not is_earnings_imminent(
        "AAPL", config_path="/nonexistent.json",
    )


def test_earnings_imminent_within_window():
    """Earnings within window → imminent."""
    tomorrow = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"AAPL": [tomorrow]}, f)
        path = f.name

    assert is_earnings_imminent("AAPL", config_path=path)


def test_earnings_not_imminent_outside_window():
    """Earnings > 5 days away → not imminent."""
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"AAPL": [future]}, f)
        path = f.name

    assert not is_earnings_imminent("AAPL", config_path=path)


# --- Reasoning ---


def test_build_reasoning_includes_factors(seeded_db):
    """Reasoning string includes multi-factor details."""
    _seed_mature_thesis(seeded_db)
    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)

    mf = MultiFactorScore(
        thesis_conviction=0.8,
        watchlist_trigger=1.0,
        news_sentiment=0.7,
        congress_alignment=0.8,
        weighted_total=0.72,
    )
    trigger = {
        "type": "daily_move",
        "price": 150.0,
        "change_percent": 3.5,
        "direction": "up",
    }

    reasoning = sg._build_reasoning(
        "BUY", "NVDA", thesis, trigger, mf,
    )
    assert "watchlist trigger hit" in reasoning
    assert "positive news sentiment" in reasoning
    assert "congress buying aligned" in reasoning
    assert "0.72" in reasoning


# --- Congress Scoring Integration ---


def _seed_congress_data(db, symbol="NVDA"):
    """Seed congress_trades and politician_scores for testing."""
    # Ensure politician_scores table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS politician_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            politician TEXT UNIQUE NOT NULL,
            total_trades INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            score REAL DEFAULT 0,
            tier TEXT DEFAULT 'unknown',
            trade_size_preference TEXT,
            filing_delay_avg_days REAL DEFAULT 0,
            committees TEXT DEFAULT '[]',
            best_sectors TEXT DEFAULT '[]',
            avg_return_90d REAL,
            last_updated TEXT
        )
    """)
    db.connect().commit()

    traded_date = (datetime.now(UTC) - timedelta(days=10)).strftime("%Y-%m-%d")
    filed_date = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

    db.execute(
        """INSERT INTO congress_trades
           (politician, symbol, action, amount_range, date_traded, date_filed)
           VALUES (?, ?, 'buy', '$100,001 - $250,000', ?, ?)""",
        ("Nancy Pelosi", symbol, traded_date, filed_date),
    )
    db.execute(
        """INSERT INTO congress_trades
           (politician, symbol, action, amount_range, date_traded, date_filed)
           VALUES (?, ?, 'buy', '$50,001 - $100,000', ?, ?)""",
        ("Dan Crenshaw", symbol, traded_date, filed_date),
    )
    db.execute(
        """INSERT INTO politician_scores
           (politician, total_trades, win_rate, score, tier, committees)
           VALUES ('Nancy Pelosi', 50, 72.0, 85.0, 'whale', '["Financial Services"]')""",
    )
    db.execute(
        """INSERT INTO politician_scores
           (politician, total_trades, win_rate, score, tier, committees)
           VALUES ('Dan Crenshaw', 20, 55.0, 48.0, 'average', '["Armed Services"]')""",
    )
    db.connect().commit()


def test_congress_alignment_uses_scorer(seeded_db):
    """Congress alignment should use PoliticianScorer.score_trade() for weighting."""
    _seed_mature_thesis(seeded_db)
    _seed_congress_data(seeded_db, "NVDA")
    sg = _make_generator(seeded_db)

    score = sg._get_congress_alignment("NVDA")
    # Both trades are buys → score should be > 0.5 (bullish)
    assert score > 0.5
    assert score <= 1.0


def test_congress_alignment_no_trades(seeded_db):
    """No congress trades → neutral 0.5."""
    _seed_mature_thesis(seeded_db)
    sg = _make_generator(seeded_db)
    assert sg._get_congress_alignment("AAPL") == 0.5


def test_congress_reasoning_with_trades(seeded_db):
    """Congress reasoning should include enriched trade details."""
    _seed_mature_thesis(seeded_db)
    _seed_congress_data(seeded_db, "NVDA")
    sg = _make_generator(seeded_db)

    reasoning = sg._get_congress_reasoning("NVDA")
    assert reasoning is not None
    assert "Pelosi" in reasoning
    assert "NVDA" in reasoning


def test_congress_reasoning_no_trades(seeded_db):
    """No recent trades → None."""
    _seed_mature_thesis(seeded_db)
    sg = _make_generator(seeded_db)
    assert sg._get_congress_reasoning("AAPL") is None


def test_signal_reasoning_includes_congress(seeded_db):
    """Full signal reasoning should include congress detail when available."""
    _seed_mature_thesis(seeded_db)
    _seed_congress_data(seeded_db, "NVDA")
    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)

    mf = MultiFactorScore(
        thesis_conviction=0.8,
        congress_alignment=0.8,
        weighted_total=0.72,
    )

    reasoning = sg._build_reasoning("BUY", "NVDA", thesis, None, mf)
    assert "Congress:" in reasoning
    assert "Pelosi" in reasoning
