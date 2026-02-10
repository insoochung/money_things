"""Tests for the database seeding module (db.seed).

This module tests each seed function in isolation. The seed module populates the
database with initial data derived from the user's money_journal (historical
investment data, existing positions, principles, etc.) to bootstrap the money_moves
system with real portfolio state.

Each seed function is tested independently using the fresh ``db`` fixture (empty
schema). Some seed functions depend on data from prior seeds (e.g., seed_lots requires
positions to exist, seed_positions requires accounts), so tests call prerequisite
seed functions explicitly.

Tests cover:
    - **seed_accounts** (test_seed_accounts): Verifies 3 accounts are created
      (Schwab brokerage, 401k, and Vanguard IRA). Checks that exactly one
      Schwab account exists.

    - **seed_trading_windows** (test_seed_trading_windows): Verifies 4 META
      trading windows are created (quarterly open/close periods reflecting
      employer trading restrictions).

    - **seed_positions** (test_seed_positions): Verifies 2 positions are created
      (META and QCOM from the money_journal). Checks META has 230 shares.

    - **seed_lots** (test_seed_lots): Verifies 11 lots are created (8 META lots +
      3 QCOM lots). Requires accounts and positions to exist first.

    - **seed_principles** (test_seed_principles): Verifies 4 principles are seeded.
      Checks a specific principle ('Domain expertise...') has the expected
      validated_count and category.

    - **seed_congress_trades** (test_seed_congress_trades): Verifies 7 congress
      trades are seeded, all attributed to Nancy Pelosi.

    - **seed_watchlist_signals** (test_seed_watchlist_signals): Verifies 6
      watchlist-derived signals are created.

    - **seed_risk_limits** (test_seed_risk_limits): Verifies 7 risk limits are
      seeded with correct default values (e.g., max_position_pct=0.15).

    - **seed_kill_switch** (test_seed_kill_switch): Verifies the kill switch is
      seeded as inactive.

    - **seed_theses** (test_seed_theses): Verifies thesis seeding from money_journal
      research markdown files. This test is conditional -- it skips if the
      ~/workspace/money_journal/research directory doesn't exist.
"""

from __future__ import annotations

from pathlib import Path

from db.database import Database
from db.seed import (
    seed_accounts,
    seed_congress_trades,
    seed_kill_switch,
    seed_lots,
    seed_positions,
    seed_principles,
    seed_risk_limits,
    seed_theses,
    seed_trading_windows,
    seed_watchlist_signals,
)


def test_seed_accounts(db: Database) -> None:
    """Verify that seed_accounts() creates exactly 3 accounts.

    The three accounts represent the user's real brokerage accounts:
    Schwab individual brokerage, Schwab 401k, and Vanguard IRA. Checks
    that exactly one account has 'Schwab' in its broker field.
    """
    count = seed_accounts(db)
    assert count == 3
    rows = db.fetchall("SELECT * FROM accounts")
    assert len(rows) == 3
    schwab = [r for r in rows if "Schwab" in r["broker"]]
    assert len(schwab) == 1


def test_seed_trading_windows(db: Database) -> None:
    """Verify that seed_trading_windows() creates 4 META trading windows.

    Trading windows define when the user is allowed to trade META stock
    (employer restriction). Each window has an opens and closes date
    corresponding to quarterly open trading periods.
    """
    count = seed_trading_windows(db)
    assert count == 4
    rows = db.fetchall("SELECT * FROM trading_windows WHERE symbol = 'META'")
    assert len(rows) == 4


def test_seed_positions(db: Database) -> None:
    """Verify that seed_positions() creates 2 positions with META having 230 shares."""
    seed_accounts(db)
    count = seed_positions(db)
    assert count == 2
    meta = db.fetchone("SELECT * FROM positions WHERE symbol = 'META'")
    assert meta["shares"] == 230


def test_seed_lots(db: Database) -> None:
    """Verify that seed_lots() creates 11 lots (8 META + 3 QCOM)."""
    seed_accounts(db)
    seed_positions(db)
    count = seed_lots(db)
    assert count == 11  # 8 META + 3 QCOM

    meta_lots = db.fetchall("SELECT * FROM lots WHERE symbol = 'META'")
    assert len(meta_lots) == 8
    qcom_lots = db.fetchall("SELECT * FROM lots WHERE symbol = 'QCOM'")
    assert len(qcom_lots) == 3


def test_seed_principles(db: Database) -> None:
    """Verify that seed_principles() creates 4 principles with correct attributes."""
    count = seed_principles(db)
    assert count == 4

    p3 = db.fetchone("SELECT * FROM principles WHERE text LIKE '%Domain expertise%'")
    assert p3 is not None
    assert p3["validated_count"] == 2
    assert p3["category"] == "domain"


def test_seed_congress_trades(db: Database) -> None:
    """Verify that seed_congress_trades() creates 7 Nancy Pelosi trades."""
    count = seed_congress_trades(db)
    assert count == 7
    pelosi = db.fetchall("SELECT * FROM congress_trades WHERE politician = 'Nancy Pelosi'")
    assert len(pelosi) == 7


def test_seed_watchlist_signals(db: Database) -> None:
    """Verify that seed_watchlist_signals() creates 6 initial signals."""
    count = seed_watchlist_signals(db)
    assert count == 6


def test_seed_risk_limits(db: Database) -> None:
    """Verify that seed_risk_limits() creates 7 risk limits with max_position_pct=0.15."""
    count = seed_risk_limits(db)
    assert count == 7
    row = db.fetchone("SELECT * FROM risk_limits WHERE limit_type = 'max_position_pct'")
    assert row["value"] == 0.15


def test_seed_kill_switch(db: Database) -> None:
    """Verify that seed_kill_switch() creates 1 inactive kill switch entry."""
    count = seed_kill_switch(db)
    assert count == 1
    row = db.fetchone("SELECT * FROM kill_switch ORDER BY id DESC LIMIT 1")
    assert not row["active"]


def test_seed_theses(db: Database) -> None:
    """Verify thesis seeding from money_journal research files (conditional)."""
    journal_research = Path.home() / "workspace" / "money_journal" / "research"
    if not journal_research.exists():
        return

    count = seed_theses(db)
    assert count > 0
    theses = db.fetchall("SELECT * FROM theses")
    assert len(theses) == count
    for t in theses:
        assert t["source_module"] == "money_journal"
