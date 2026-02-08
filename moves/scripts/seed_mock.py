"""Seed the mock database with realistic portfolio data for development.

Creates a fully-populated mock database with positions, theses, signals, trades,
and performance history so the dashboard and API endpoints return meaningful data.

Usage:
    cd ~/workspace/money/moves
    python3 scripts/seed_mock.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

# Ensure moves root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MOVES_TESTING", "1")
os.environ.setdefault("MOVES_SESSION_SECRET_KEY", "dev-secret")

from config.settings import get_settings
from db.database import Database


def seed_mock() -> None:
    """Seed the mock database with realistic development data."""
    settings = get_settings()
    db_path = settings.get_db_path()
    print(f"Seeding database at {db_path}")

    db = Database(db_path)
    db.init_schema()

    _seed_accounts(db)
    _seed_theses(db)
    _seed_positions(db)
    _seed_lots(db)
    _seed_signals(db)
    _seed_trades(db)
    _seed_portfolio_value(db)
    _seed_risk_limits(db)
    _seed_kill_switch(db)
    _seed_principles(db)
    _seed_congress_trades(db)

    db.close()
    print("✅ Mock database seeded successfully")


def _seed_accounts(db: Database) -> None:
    """Seed brokerage accounts."""
    db.execute(
        """INSERT OR IGNORE INTO accounts
           (name, broker, account_type, account_hash, purpose, active)
           VALUES ('Individual (...441)', 'Charles Schwab',
                   'individual_brokerage', '441',
                   'Active trading', TRUE)"""
    )
    db.connect().commit()
    print("  ✓ accounts")


def _seed_theses(db: Database) -> None:
    """Seed investment theses with different statuses."""
    theses = [
        (
            "AI Infrastructure Capex Supercycle",
            "Hyperscalers are massively increasing capex on AI infrastructure. "
            "NVDA, AMD, and AVGO are primary beneficiaries.",
            "long",
            "active",
            json.dumps(["NVDA", "AMD", "AVGO"]),
            0.85,
        ),
        (
            "Cloud Platform Dominance",
            "MSFT and GOOG dominate enterprise cloud with Azure and GCP. "
            "AI integration drives further workload migration.",
            "long",
            "strengthening",
            json.dumps(["MSFT", "GOOG"]),
            0.75,
        ),
        (
            "Consumer Tech Ecosystem Lock-in",
            "AAPL ecosystem stickiness drives recurring services revenue. "
            "Vision Pro creates new platform opportunity.",
            "long",
            "active",
            json.dumps(["AAPL"]),
            0.65,
        ),
        (
            "EV and Autonomy Disruption",
            "TSLA leads in EV manufacturing scale and autonomous driving data. "
            "Risk: valuation premium requires continued execution.",
            "long",
            "weakening",
            json.dumps(["TSLA"]),
            0.40,
        ),
    ]
    for t in theses:
        db.execute(
            """INSERT INTO theses (title, thesis_text, strategy, status, symbols, conviction)
               VALUES (?, ?, ?, ?, ?, ?)""",
            t,
        )
    db.connect().commit()
    print("  ✓ theses")


def _seed_positions(db: Database) -> None:
    """Seed stock positions with realistic data."""
    account = db.fetchone("SELECT id FROM accounts LIMIT 1")
    account_id = account["id"] if account else 1

    positions = [
        (account_id, "NVDA", 45, 142.50, "long", "AI infrastructure", 1),
        (account_id, "AAPL", 80, 189.25, "long", "Ecosystem play", 3),
        (account_id, "MSFT", 35, 415.80, "long", "Cloud + AI", 2),
        (account_id, "GOOG", 60, 178.40, "long", "Cloud + Search AI", 2),
        (account_id, "TSLA", 25, 248.90, "long", "EV/Autonomy", 4),
        (account_id, "AMD", 55, 165.30, "long", "AI chips", 1),
        (account_id, "AMZN", 40, 198.60, "long", "Cloud + retail", None),
    ]
    for p in positions:
        db.execute(
            """INSERT INTO positions
               (account_id, symbol, shares, avg_cost, side, strategy, thesis_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            p,
        )
    db.connect().commit()
    print("  ✓ positions")


def _seed_lots(db: Database) -> None:
    """Seed tax lots for each position."""
    positions = db.fetchall("SELECT id, account_id, symbol, shares, avg_cost FROM positions")
    for pos in positions:
        # Create 1-2 lots per position
        db.execute(
            """INSERT INTO lots
               (position_id, account_id, symbol, shares,
                cost_basis, acquired_date, source, holding_period)
               VALUES (?, ?, ?, ?, ?, ?, 'trade', 'Short Term')""",
            (
                pos["id"],
                pos["account_id"],
                pos["symbol"],
                pos["shares"],
                pos["shares"] * pos["avg_cost"],
                "2025-09-15",
            ),
        )
    db.connect().commit()
    print("  ✓ lots")


def _seed_signals(db: Database) -> None:
    """Seed recent signals with mixed statuses."""
    now = datetime.now(UTC)
    signals = [
        ("BUY", "NVDA", 1, 0.82, "thesis_update", "AI capex thesis strengthening", "approved"),
        ("BUY", "AMD", 1, 0.71, "thesis_update", "Complementary AI chip play", "approved"),
        ("SELL", "TSLA", 4, 0.55, "news_event", "Delivery miss concerns", "rejected"),
        ("BUY", "GOOG", 2, 0.68, "congress_trade", "Pelosi bought GOOG calls", "pending"),
        ("BUY", "AMZN", None, 0.60, "manual", "AWS growth reacceleration", "pending"),
        ("SELL", "AAPL", 3, 0.45, "price_trigger", "Near resistance level", "ignored"),
    ]
    for i, s in enumerate(signals):
        created = (now - timedelta(days=len(signals) - i)).isoformat()
        db.execute(
            """INSERT INTO signals
               (action, symbol, thesis_id, confidence, source, reasoning, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (*s, created),
        )
    db.connect().commit()
    print("  ✓ signals")


def _seed_trades(db: Database) -> None:
    """Seed recent trade history."""
    now = datetime.now(UTC)
    trades = [
        (1, "NVDA", "BUY", 15, 138.50, 2077.50, 0, "mock", None),
        (1, "NVDA", "BUY", 10, 141.20, 1412.00, 0, "mock", None),
        (1, "NVDA", "BUY", 20, 145.80, 2916.00, 0, "mock", None),
        (2, "AMD", "BUY", 20, 162.40, 3248.00, 0, "mock", None),
        (2, "AMD", "BUY", 15, 158.90, 2383.50, 0, "mock", None),
        (None, "AAPL", "BUY", 40, 185.30, 7412.00, 0, "mock", None),
        (None, "AAPL", "BUY", 40, 193.20, 7728.00, 0, "mock", None),
        (None, "MSFT", "BUY", 35, 415.80, 14553.00, 0, "mock", None),
        (None, "GOOG", "BUY", 30, 175.60, 5268.00, 0, "mock", None),
        (None, "GOOG", "BUY", 30, 181.20, 5436.00, 0, "mock", None),
        (None, "TSLA", "BUY", 25, 248.90, 6222.50, 0, "mock", None),
        (None, "AMZN", "BUY", 40, 198.60, 7944.00, 0, "mock", None),
        (None, "AMD", "BUY", 20, 170.10, 3402.00, 0, "mock", None),
        (None, "NVDA", "SELL", 5, 150.20, 751.00, 0, "mock", 58.50),
    ]
    for i, t in enumerate(trades):
        ts = (now - timedelta(days=len(trades) - i, hours=10)).isoformat()
        db.execute(
            """INSERT INTO trades
               (signal_id, symbol, action, shares, price,
                total_value, fees, broker, realized_pnl, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (*t, ts),
        )
    db.connect().commit()
    print("  ✓ trades")


def _seed_portfolio_value(db: Database) -> None:
    """Seed portfolio value history for the past 30 days."""
    now = datetime.now(UTC)
    base_value = 95000.0
    cash = 18500.0
    cost_basis = 82000.0

    for i in range(30, -1, -1):
        date = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        # Simulate gradual growth with some volatility
        import math

        noise = math.sin(i * 0.5) * 1500 + (30 - i) * 150
        total = round(base_value + noise, 2)
        daily_ret = round(noise / base_value * 100, 2) if i < 30 else 0.0
        db.execute(
            """INSERT OR IGNORE INTO portfolio_value
               (date, total_value, long_value, short_value, cash, cost_basis, daily_return_pct)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (date, total, total - cash, cash, cost_basis, daily_ret),
        )
    db.connect().commit()
    print("  ✓ portfolio_value")


def _seed_risk_limits(db: Database) -> None:
    """Seed default risk limits."""
    limits = [
        ("max_position_pct", 0.15),
        ("max_sector_pct", 0.35),
        ("max_gross_exposure", 1.50),
        ("net_exposure_min", -0.30),
        ("net_exposure_max", 1.30),
        ("max_drawdown", 0.20),
        ("daily_loss_limit", 0.03),
    ]
    for lt, val in limits:
        db.execute(
            "INSERT OR IGNORE INTO risk_limits (limit_type, value) VALUES (?, ?)",
            (lt, val),
        )
    db.connect().commit()
    print("  ✓ risk_limits")


def _seed_kill_switch(db: Database) -> None:
    """Seed kill switch as inactive."""
    db.execute("INSERT INTO kill_switch (active, reason) VALUES (FALSE, 'Initial state')")
    db.connect().commit()
    print("  ✓ kill_switch")


def _seed_principles(db: Database) -> None:
    """Seed investment principles."""
    principles = [
        ("Domain expertise creates durable edge — lean into it", "domain", "journal_import", 2),
        ("Insider experience is high-signal for conviction", "conviction", "journal_import", 0),
        ("Avoid legacy tech with rigid structures", "domain", "journal_import", 0),
        ("Position sizing matters more than entry price", "risk", "learned", 1),
    ]
    for p in principles:
        db.execute(
            """INSERT INTO principles (text, category, origin, validated_count, weight)
               VALUES (?, ?, ?, ?, 0.05)""",
            p,
        )
    db.connect().commit()
    print("  ✓ principles")


def _seed_congress_trades(db: Database) -> None:
    """Seed congressional trading data."""
    trades = [
        ("Nancy Pelosi", "NVDA", "BUY", "$1M-$5M", "2026-01"),
        ("Nancy Pelosi", "GOOG", "BUY", "$500K-$1M", "2026-01"),
        ("Nancy Pelosi", "AMZN", "BUY", "$1M-$5M", "2026-01"),
        ("Nancy Pelosi", "AAPL", "SELL", "$250K-$500K", "2026-01"),
        ("Tommy Tuberville", "NVDA", "BUY", "$100K-$250K", "2026-02"),
    ]
    for t in trades:
        db.execute(
            """INSERT INTO congress_trades
               (politician, symbol, action, amount_range, date_filed)
               VALUES (?, ?, ?, ?, ?)""",
            t,
        )
    db.connect().commit()
    print("  ✓ congress_trades")


if __name__ == "__main__":
    seed_mock()
