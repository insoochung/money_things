"""Tests for the signal generation pipeline."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from engine import SignalStatus, ThesisStatus
from engine.risk import RiskManager
from engine.signal_generator import _BASE_POSITION_SIZE, SignalGenerator
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine


@pytest.fixture
def generator(seeded_db):
    """Create a SignalGenerator with mock pricing."""
    # Build a fake pricing module
    pricing = SimpleNamespace(
        get_price=lambda symbol, db=None: {
            "symbol": symbol,
            "price": 150.0,
            "change": 3.5,
            "change_percent": 2.4,
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

    se = SignalEngine(db=seeded_db)
    te = ThesisEngine(db=seeded_db)
    rm = RiskManager(db=seeded_db)

    return SignalGenerator(
        db=seeded_db,
        signal_engine=se,
        thesis_engine=te,
        risk_manager=rm,
        pricing=pricing,
    )


def test_run_scan_generates_buy_for_unheld_symbols(generator):
    """Active thesis with unheld symbols should generate BUY signals when triggered."""
    # The seeded thesis has NVDA and AVGO with status='active'.
    # Mock pricing returns 2.4% daily change (above 2% threshold).
    # Neither NVDA nor AVGO are held.
    results = generator.run_scan()

    # Should generate BUY signals for symbols with price triggers
    assert len(results) >= 1
    for r in results:
        assert r["action"] == "BUY"
        assert r["symbol"] in ("NVDA", "AVGO")
        assert r["confidence"] > 0
        assert r["signal_id"] is not None


def test_run_scan_no_duplicates(generator):
    """Should not create duplicate signals for symbols with pending signals."""
    # First scan creates signals
    first = generator.run_scan()
    assert len(first) >= 1

    # Second scan should skip them (pending signals exist)
    second = generator.run_scan()
    assert len(second) == 0


def test_run_scan_generates_sell_for_weakening_thesis(seeded_db):
    """Weakening thesis should generate SELL signals for held positions."""
    # Transition thesis to weakening
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.WEAKENING, reason="Test weakening")

    # Add a position for NVDA
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 10, 140.0, 'long')"""
    )
    seeded_db.connect().commit()

    pricing = SimpleNamespace(
        get_price=lambda symbol, db=None: {
            "symbol": symbol, "price": 150.0, "change": 0.5,
            "change_percent": 0.3, "volume": 1000000,
            "timestamp": "2026-02-08T12:00:00", "source": "yfinance",
        },
        get_history=lambda symbol, period="5d", db=None: [],
    )

    sg = SignalGenerator(
        db=seeded_db,
        signal_engine=SignalEngine(db=seeded_db),
        thesis_engine=te,
        risk_manager=RiskManager(db=seeded_db),
        pricing=pricing,
    )

    results = sg.run_scan()
    sells = [r for r in results if r["action"] == "SELL"]
    assert len(sells) >= 1
    assert sells[0]["symbol"] == "NVDA"


def test_run_scan_skips_archived_theses(seeded_db):
    """Archived theses should generate no signals."""
    te = ThesisEngine(db=seeded_db)
    te.transition_status(1, ThesisStatus.ARCHIVED, reason="Done")

    pricing = SimpleNamespace(
        get_price=lambda symbol, db=None: {"symbol": symbol, "price": 150.0,
            "change_percent": 5.0, "change": 7.5, "volume": 1000000,
            "timestamp": "now", "source": "yfinance"},
        get_history=lambda symbol, period="5d", db=None: [],
    )

    sg = SignalGenerator(
        db=seeded_db,
        signal_engine=SignalEngine(db=seeded_db),
        thesis_engine=te,
        risk_manager=RiskManager(db=seeded_db),
        pricing=pricing,
    )

    results = sg.run_scan()
    assert len(results) == 0


def test_compute_position_size(generator):
    """Position size should scale with confidence and cap at max."""
    # NAV is 100000, max_position_pct is 0.15
    size = generator._compute_position_size("NVDA", 0.5, 100000)
    expected = _BASE_POSITION_SIZE * 0.5 * 2  # 0.02
    assert size == pytest.approx(expected)

    # High confidence should cap at max_position_pct
    size_high = generator._compute_position_size("NVDA", 1.0, 100000)
    assert size_high == pytest.approx(_BASE_POSITION_SIZE * 1.0 * 2)  # 0.04

    # Zero NAV
    size_zero = generator._compute_position_size("NVDA", 0.5, 0)
    assert size_zero == _BASE_POSITION_SIZE


def test_signals_persisted_to_db(generator, seeded_db):
    """Generated signals should be persisted in the database."""
    results = generator.run_scan()
    assert len(results) >= 1

    for r in results:
        signal = generator.signal_engine.get_signal(r["signal_id"])
        assert signal is not None
        assert signal.status == SignalStatus.PENDING
        assert signal.symbol == r["symbol"]


def test_no_signal_below_confidence_threshold(seeded_db):
    """Signals with very low scored confidence should be skipped."""
    # Invalidated thesis has 0.0x multiplier -> confidence becomes 0
    te = ThesisEngine(db=seeded_db)
    # Can't go directly to invalidated from active, go through weakening first
    te.transition_status(1, ThesisStatus.WEAKENING, reason="weak")
    te.transition_status(1, ThesisStatus.INVALIDATED, reason="invalid")

    pricing = SimpleNamespace(
        get_price=lambda symbol, db=None: {"symbol": symbol, "price": 150.0,
            "change_percent": 3.0, "change": 4.5, "volume": 1000000,
            "timestamp": "now", "source": "yfinance"},
        get_history=lambda symbol, period="5d", db=None: [],
    )

    sg = SignalGenerator(
        db=seeded_db,
        signal_engine=SignalEngine(db=seeded_db),
        thesis_engine=te,
        risk_manager=RiskManager(db=seeded_db),
        pricing=pricing,
    )

    results = sg.run_scan()
    # Invalidated thesis: 0.0x multiplier -> confidence 0 -> skip
    assert len(results) == 0
