"""Scheduled job implementations for the money_moves system.

Each function is a standalone job callable that receives engine references
and performs a specific periodic task. Jobs are designed to be called by
the APScheduler wrapper (engine/scheduler.py) from the FastAPI lifespan.

All jobs include error handling and logging. Jobs that need market hours
checks use is_market_hours() to skip execution outside trading hours.
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


def is_market_hours() -> bool:
    """Check if current time is within US market hours (9:30-16:00 ET, Mon-Fri).

    Returns:
        True if within regular trading hours.
    """
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    return market_open <= now_et <= market_close


def job_price_update(db: Database) -> None:
    """Update prices for all open positions.

    Fetches current prices for each symbol in the positions table.
    Only runs during market hours (enforced by cron trigger).
    """
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
    """Expire pending signals older than 24 hours.

    Finds signals with status='pending' created more than 24h ago and
    marks them as 'ignored' via the signal engine's expire method.
    """
    cutoff = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    rows = db.fetchall(
        "SELECT id, symbol FROM signals WHERE status = 'pending' AND created_at < ?",
        (cutoff,),
    )
    if not rows:
        logger.info("signal_expiry: no expired signals")
        return

    logger.info("signal_expiry: expiring %d signals", len(rows))
    for row in rows:
        try:
            # Get current price for the what-if tracking
            from engine import pricing

            price_data = pricing.get_price(row["symbol"], db=db)
            price = price_data.get("price", 0)
            signal_engine.expire_signal(row["id"], price_at_pass=price)
            logger.info("signal_expiry: expired signal %d (%s)", row["id"], row["symbol"])
        except Exception:
            logger.exception("signal_expiry: failed to expire signal %d", row["id"])


def job_nav_snapshot(analytics: AnalyticsEngine) -> None:
    """Record portfolio NAV to portfolio_value table.

    Delegates to AnalyticsEngine.snapshot_nav() which queries positions,
    computes total value, and inserts into portfolio_value.
    """
    logger.info("nav_snapshot: recording NAV")
    analytics.snapshot_nav()
    logger.info("nav_snapshot: complete")


def job_whatif_update(whatif: WhatIfEngine) -> None:
    """Update current prices for all what-if entries.

    Delegates to WhatIfEngine.update_all() which refreshes hypothetical P&L
    for all tracked rejected/ignored signals.
    """
    logger.info("whatif_update: updating all what-if entries")
    count = whatif.update_all()
    logger.info("whatif_update: updated %d entries", count)


def job_congress_trades(congress: CongressTradesEngine) -> None:
    """Scrape recent congressional trades.

    Delegates to CongressTradesEngine.fetch_recent() and store_trades().
    """
    logger.info("congress_trades: fetching recent trades")
    trades = congress.fetch_recent(days=3)
    if trades:
        stored = congress.store_trades(trades)
        logger.info("congress_trades: stored %d new trades", stored)
    else:
        logger.info("congress_trades: no new trades found")


def job_exposure_snapshot(analytics: AnalyticsEngine) -> None:
    """Record exposure breakdown to exposure_snapshots table.

    Delegates to AnalyticsEngine.snapshot_exposure().
    """
    logger.info("exposure_snapshot: recording exposure")
    analytics.snapshot_exposure()
    logger.info("exposure_snapshot: complete")


def job_stale_thesis_check(thesis_engine: ThesisEngine, db: Database) -> None:
    """Flag theses older than 30 days without updates as weakening.

    Queries active theses whose last update (updated_at or created_at) is
    older than 30 days and transitions them to 'weakening' status.
    """
    cutoff = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    rows = db.fetchall(
        "SELECT id FROM theses WHERE status = 'active' AND "
        "COALESCE(updated_at, created_at) < ?",
        (cutoff,),
    )
    if not rows:
        logger.info("stale_thesis_check: no stale theses")
        return

    logger.info("stale_thesis_check: found %d stale theses", len(rows))
    for row in rows:
        try:
            thesis_engine.transition_status(
                row["id"],
                new_status="weakening",
                reason="Auto-flagged: no update in 30+ days",
            )
            logger.info("stale_thesis_check: flagged thesis %d as weakening", row["id"])
        except Exception:
            logger.exception("stale_thesis_check: failed to flag thesis %d", row["id"])
