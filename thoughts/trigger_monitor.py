"""Watchlist trigger proximity monitor.

Checks live prices against active watchlist triggers and returns
alerts for triggers that are close to firing. Designed to be called
from heartbeat checks or scheduled jobs.

Functions:
    check_triggers: Check all active triggers against live prices.
    format_alerts: Format trigger alerts for Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from commands import _fetch_prices
from engine import ThoughtsEngine

logger = logging.getLogger(__name__)

# Alert thresholds (percentage distance from trigger)
THRESHOLD_CRITICAL = 3.0  # ⚠️ Very close
THRESHOLD_WARNING = 7.0  # 👀 Getting close
THRESHOLD_WATCH = 15.0  # 📍 On radar


def check_triggers(
    engine: ThoughtsEngine | None = None,
) -> list[dict[str, Any]]:
    """Check all active watchlist triggers against live prices.

    Args:
        engine: ThoughtsEngine instance (creates one if None).

    Returns:
        List of alert dicts sorted by urgency, each with:
            - symbol: Ticker symbol
            - trigger_type: entry/exit/stop_loss/take_profit
            - target: Target price
            - current: Current price
            - pct_away: Percentage distance (signed)
            - level: 'critical', 'warning', or 'watch'
            - trigger_id: DB trigger ID
    """
    if engine is None:
        engine = ThoughtsEngine()

    triggers = engine._moves_query(
        "SELECT * FROM watchlist_triggers WHERE active = 1"
    )
    if not triggers:
        return []

    # Collect all symbols needed
    symbols = sorted({tr["symbol"].upper() for tr in triggers})
    prices = _fetch_prices(symbols)
    if not prices:
        return []

    alerts: list[dict[str, Any]] = []
    for tr in triggers:
        sym = tr["symbol"].upper()
        current = prices.get(sym)
        if not current:
            continue

        target = tr["target_value"]
        pct_away = ((target - current) / current) * 100

        abs_pct = abs(pct_away)
        if abs_pct <= THRESHOLD_CRITICAL:
            level = "critical"
        elif abs_pct <= THRESHOLD_WARNING:
            level = "warning"
        elif abs_pct <= THRESHOLD_WATCH:
            level = "watch"
        else:
            continue  # Too far away, skip

        alerts.append({
            "symbol": sym,
            "trigger_type": tr["trigger_type"],
            "target": target,
            "current": current,
            "pct_away": pct_away,
            "level": level,
            "trigger_id": tr["id"],
            "thesis_id": tr.get("thesis_id"),
            "notes": tr.get("notes"),
        })

    # Sort by urgency: critical first, then by absolute distance
    level_order = {"critical": 0, "warning": 1, "watch": 2}
    alerts.sort(key=lambda a: (level_order[a["level"]], abs(a["pct_away"])))
    return alerts


def format_alerts(alerts: list[dict[str, Any]]) -> str | None:
    """Format trigger alerts for Telegram notification.

    Only includes critical and warning alerts (not watch-level).

    Args:
        alerts: List of alert dicts from check_triggers().

    Returns:
        Formatted message string, or None if no actionable alerts.
    """
    actionable = [a for a in alerts if a["level"] in ("critical", "warning")]
    if not actionable:
        return None

    lines: list[str] = ["🎯 **Trigger Alert**\n"]
    for a in actionable:
        emoji = "⚠️" if a["level"] == "critical" else "👀"
        direction = "↑" if a["pct_away"] > 0 else "↓"
        ttype = a["trigger_type"].replace("_", " ")
        lines.append(
            f"{emoji} **{a['symbol']}** {ttype}: "
            f"${a['target']:.0f} ({direction}{abs(a['pct_away']):.1f}% "
            f"from ${a['current']:.2f})"
        )
        if a.get("notes"):
            lines.append(f"   _{a['notes']}_")

    return "\n".join(lines)
