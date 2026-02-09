"""Reset and re-seed the mock database cleanly (no duplicates)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MOVES_TESTING", "1")
os.environ.setdefault("MOVES_SESSION_SECRET_KEY", "dev-secret")

from config.settings import get_settings
from db.database import Database


def reset_mock() -> None:
    """Delete all data from all tables and re-seed."""
    settings = get_settings()
    db_path = settings.get_db_path()
    print(f"Resetting database at {db_path}")

    db = Database(db_path)
    db.init_schema()

    # Delete in FK-safe order
    tables = [
        "what_if",
        "trades",
        "orders",
        "lots",
        "signals",
        "signal_scores",
        "positions",
        "congress_trades",
        "principles",
        "drawdown_events",
        "exposure_snapshots",
        "portfolio_value",
        "risk_limits",
        "kill_switch",
        "thesis_news",
        "thesis_versions",
        "theses",
        "accounts",
        "audit_log",
        "price_history",
        "scheduled_tasks",
        "users",
    ]

    for table in tables:
        try:
            db.execute(f"DELETE FROM {table}")  # noqa: S608
        except Exception as e:
            print(f"  ⚠ {table}: {e}")
    # Reset autoincrement counters
    try:
        db.execute("DELETE FROM sqlite_sequence")
    except Exception:
        pass
    db.connect().commit()
    print("✅ All tables cleared")

    db.close()

    # Re-seed
    from scripts.seed_mock import seed_mock

    seed_mock()


if __name__ == "__main__":
    reset_mock()
