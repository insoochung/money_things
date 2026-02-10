"""Tests for gate-based signal generation."""

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
    GateResult,
    SignalGenerator,
)
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine


def _make_pricing(change_pct: float = 2.4, price: float = 150.0):
    """Create a fake pricing module."""
    return SimpleNamespace(
        get_price=lambda symbol, db=None: {
            "symbol": symbol, "price": price,
            "change_percent": change_pct, "volume": 1000000,
            "timestamp": "2026-02-08T12:00:00", "source": "yfinance",
        },
        get_history=lambda symbol, period="5d", db=None: [
            {"date": "2026-02-03", "close": 140.0},
            {"date": "2026-02-07", "close": 150.0},
        ],
    )


def _make_generator(db, pricing=None):
    return SignalGenerator(
        db=db,
        signal_engine=SignalEngine(db=db),
        thesis_engine=ThesisEngine(db=db),
        risk_manager=RiskManager(db=db),
        pricing=pricing or _make_pricing(),
    )


def _seed_mature_thesis(db, conviction=0.8, days_old=14):
    """Seed a thesis that passes all maturity gates."""
    created = (
        datetime.now(UTC) - timedelta(days=days_old)
    ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        "UPDATE theses SET created_at = ?, conviction = ? WHERE id = 1",
        (created, conviction),
    )
    for i in range(_MIN_THINK_SESSIONS):
        db.execute(
            "INSERT INTO thesis_versions "
            "(thesis_id, new_status, reason) VALUES (1, 'active', ?)",
            (f"Think session {i + 1}",),
        )
    db.connect().commit()


@pytest.fixture
def generator(seeded_db):
    _seed_mature_thesis(seeded_db)
    return _make_generator(seeded_db)


# --- Core signal generation ---


def test_run_scan_generates_buy(generator):
    """Active thesis with mature gates → BUY signals."""
    results = generator.run_scan()
    assert len(results) >= 1
    for r in results:
        assert r["action"] == "BUY"
        assert r["symbol"] in ("NVDA", "AVGO")
        assert r["confidence"] > 0
        assert r["signal_id"] is not None


def test_run_scan_dedup_updates_pending(generator):
    """Second scan updates existing pending signals."""
    first = generator.run_scan()
    assert len(first) >= 1
    second = generator.run_scan()
    for r in second:
        assert r.get("updated") is True


def test_sell_for_weakening_thesis(seeded_db):
    """Weakening thesis → SELL for held positions."""
    _seed_mature_thesis(seeded_db)
    ThesisEngine(db=seeded_db).transition_status(
        1, ThesisStatus.WEAKENING, reason="Test",
    )
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 10, 140.0, 'long')""",
    )
    seeded_db.connect().commit()

    results = _make_generator(seeded_db, _make_pricing(0.3)).run_scan()
    sells = [r for r in results if r["action"] == "SELL"]
    assert len(sells) >= 1
    assert sells[0]["symbol"] == "NVDA"


def test_archived_theses_skipped(seeded_db):
    """Archived theses generate nothing."""
    _seed_mature_thesis(seeded_db)
    ThesisEngine(db=seeded_db).transition_status(
        1, ThesisStatus.ARCHIVED, reason="Done",
    )
    assert _make_generator(seeded_db).run_scan() == []


def test_signals_persisted(generator, seeded_db):
    """Signals are written to DB."""
    for r in generator.run_scan():
        sig = generator.signal_engine.get_signal(r["signal_id"])
        assert sig is not None
        assert sig.status == SignalStatus.PENDING


def test_position_size_scales_with_confidence(generator):
    """Size = base × confidence × 2."""
    expected_half = _BASE_POSITION_SIZE * 0.5 * 2
    assert generator._compute_position_size(0.5, 100_000) == pytest.approx(
        expected_half,
    )
    expected_full = _BASE_POSITION_SIZE * 1.0 * 2
    assert generator._compute_position_size(1.0, 100_000) == pytest.approx(
        expected_full,
    )
    assert generator._compute_position_size(0.5, 0) == _BASE_POSITION_SIZE


# --- Gate checks ---


def test_gate_blocks_low_conviction(seeded_db):
    """Conviction < 70% → blocked."""
    _seed_mature_thesis(seeded_db, conviction=0.5)
    assert _make_generator(seeded_db).run_scan() == []


def test_gate_blocks_young_thesis(seeded_db):
    """Thesis < 7 days old → blocked."""
    _seed_mature_thesis(seeded_db, days_old=3)
    assert _make_generator(seeded_db).run_scan() == []


def test_gate_blocks_insufficient_think_sessions(seeded_db):
    """< 2 /think sessions → blocked."""
    created = (
        datetime.now(UTC) - timedelta(days=14)
    ).strftime("%Y-%m-%d %H:%M:%S")
    seeded_db.execute(
        "UPDATE theses SET created_at = ?, conviction = 0.8 WHERE id = 1",
        (created,),
    )
    seeded_db.connect().commit()
    assert _make_generator(seeded_db).run_scan() == []


def test_gate_blocks_trading_blackout(seeded_db):
    """Symbol in trading window blackout → blocked."""
    _seed_mature_thesis(seeded_db)
    seeded_db.execute(
        """INSERT INTO trading_windows (symbol, opens, closes, notes)
           VALUES ('NVDA', '2025-01-01', '2025-01-15', 'Past')""",
    )
    seeded_db.connect().commit()
    sg = _make_generator(seeded_db)
    assert sg._is_in_trading_blackout("NVDA") is True
    assert sg._is_in_trading_blackout("AVGO") is False


def test_sell_bypasses_gates(seeded_db):
    """SELL signals bypass conviction/age/session gates."""
    _seed_mature_thesis(seeded_db, conviction=0.5, days_old=3)
    ThesisEngine(db=seeded_db).transition_status(
        1, ThesisStatus.WEAKENING, reason="Test",
    )
    seeded_db.execute(
        """INSERT INTO positions (symbol, shares, avg_cost, side)
           VALUES ('NVDA', 10, 140.0, 'long')""",
    )
    seeded_db.connect().commit()

    sg = _make_generator(seeded_db, _make_pricing(0.3))
    sells = [r for r in sg.run_scan() if r["action"] == "SELL"]
    assert len(sells) >= 1


def test_gate_result_model():
    """GateResult defaults to passed."""
    assert GateResult().passed is True
    assert GateResult(passed=False, reason="test").reason == "test"


# --- Earnings calendar ---


def test_earnings_imminent_no_config():
    assert not is_earnings_imminent(
        "AAPL", config_path="/nonexistent.json",
    )


def test_earnings_imminent_within_window():
    tomorrow = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"AAPL": [tomorrow]}, f)
    assert is_earnings_imminent("AAPL", config_path=f.name)


def test_earnings_not_imminent_outside_window():
    future = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False,
    ) as f:
        json.dump({"AAPL": [future]}, f)
    assert not is_earnings_imminent("AAPL", config_path=f.name)


# --- Reasoning ---


def test_reasoning_includes_thesis_info(seeded_db):
    """Reasoning mentions thesis title, status, conviction."""
    _seed_mature_thesis(seeded_db)
    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    reasoning = sg._build_reasoning("BUY", "NVDA", thesis)
    assert thesis.title in reasoning
    assert "80%" in reasoning
    assert "not yet in portfolio" in reasoning


def test_sell_reasoning(seeded_db):
    """SELL reasoning mentions held status."""
    _seed_mature_thesis(seeded_db)
    ThesisEngine(db=seeded_db).transition_status(
        1, ThesisStatus.WEAKENING, reason="Test",
    )
    sg = _make_generator(seeded_db)
    thesis = sg.thesis_engine.get_thesis(1)
    reasoning = sg._build_reasoning("SELL", "NVDA", thesis)
    assert "held" in reasoning
    assert "weakening" in reasoning
