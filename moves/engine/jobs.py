"""Scheduled job implementations for the money_moves system.

Jobs now iterate over all active users or accept user_id.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from db.database import Database
    from engine.analytics import AnalyticsEngine
    from engine.congress import CongressTradesEngine
    from engine.signals import SignalEngine
    from engine.thesis import ThesisEngine
    from engine.whatif import WhatIfEngine

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


def _get_active_user_ids(db: Database) -> list[int]:
    """Get all active user IDs from the users table.

    Returns:
        List of active user IDs. Falls back to [1] if users table doesn't exist yet.
    """
    try:
        rows = db.fetchall("SELECT id FROM users WHERE active = TRUE")
        return [r["id"] for r in rows] if rows else [1]
    except Exception:
        return [1]


def is_market_hours() -> bool:
    """Check if current time is within US market hours (9:30-16:00 ET, Mon-Fri)."""
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def job_price_update(db: Database) -> None:
    """Update prices for all open positions (global — prices are shared)."""
    from engine import pricing

    rows = db.fetchall(
        "SELECT DISTINCT symbol FROM positions WHERE status = 'open'"
    )
    if not rows:
        logger.info("price_update: no open positions")
        return

    symbols = [r["symbol"] for r in rows]
    logger.info("price_update: updating %d symbols", len(symbols))
    results = pricing.get_prices(symbols, db=db)
    logger.info("price_update: got prices for %d/%d symbols", len(results), len(symbols))


def job_signal_expiry(signal_engine: SignalEngine, db: Database) -> None:
    """Expire pending signals older than 24 hours for all users."""
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    rows = db.fetchall(
        "SELECT id, symbol, user_id FROM signals WHERE status = 'pending' AND created_at < ?",
        (cutoff,),
    )
    if not rows:
        logger.info("signal_expiry: no expired signals")
        return

    logger.info("signal_expiry: expiring %d signals", len(rows))
    for row in rows:
        try:
            from engine import pricing

            price_data = pricing.get_price(row["symbol"], db=db)
            price = price_data.get("price", 0)
            signal_engine.expire_signal(row["id"], row["user_id"], price_at_pass=price)
            logger.info("signal_expiry: expired signal %d (%s)", row["id"], row["symbol"])
        except Exception:
            logger.exception("signal_expiry: failed to expire signal %d", row["id"])


def job_nav_snapshot(analytics: AnalyticsEngine, db: Database) -> None:
    """Record portfolio NAV for all active users."""
    for user_id in _get_active_user_ids(db):
        logger.info("nav_snapshot: recording NAV for user %d", user_id)
        analytics.snapshot_nav(user_id)
    logger.info("nav_snapshot: complete")


def job_whatif_update(whatif: WhatIfEngine, db: Database) -> None:
    """Update what-if entries for all active users."""
    for user_id in _get_active_user_ids(db):
        logger.info("whatif_update: updating for user %d", user_id)
        count = whatif.update_all(user_id)
        logger.info("whatif_update: updated %d entries for user %d", count, user_id)


def job_congress_trades(congress: CongressTradesEngine) -> None:
    """Scrape recent congressional trades (global — no user_id for scraping)."""
    logger.info("congress_trades: fetching recent trades")
    trades = congress.fetch_recent(days=3)
    if trades:
        stored = congress.store_trades(trades)
        logger.info("congress_trades: stored %d new trades", stored)
    else:
        logger.info("congress_trades: no new trades found")


def job_exposure_snapshot(analytics: AnalyticsEngine, db: Database) -> None:
    """Record exposure breakdown for all active users."""
    for user_id in _get_active_user_ids(db):
        logger.info("exposure_snapshot: recording exposure for user %d", user_id)
        analytics.snapshot_exposure(user_id)
    logger.info("exposure_snapshot: complete")


def job_stale_thesis_check(thesis_engine: ThesisEngine, db: Database) -> None:
    """Flag stale theses as weakening for all active users."""
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    for user_id in _get_active_user_ids(db):
        rows = db.fetchall(
            "SELECT id FROM theses WHERE status = 'active' AND user_id = ? AND "
            "COALESCE(updated_at, created_at) < ?",
            (user_id, cutoff),
        )
        if not rows:
            logger.info("stale_thesis_check: no stale theses for user %d", user_id)
            continue

        logger.info("stale_thesis_check: found %d stale theses for user %d", len(rows), user_id)
        for row in rows:
            try:
                thesis_engine.transition_status(
                    row["id"],
                    new_status="weakening",
                    reason="Auto-flagged: no update in 30+ days",
                    user_id=user_id,
                )
                logger.info("stale_thesis_check: flagged thesis %d as weakening", row["id"])
            except Exception:
                logger.exception("stale_thesis_check: failed to flag thesis %d", row["id"])
