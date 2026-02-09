"""Earnings calendar blocking: prevents signals near earnings dates.

Checks whether a symbol has an upcoming earnings report within a configurable
window (default 5 days). Uses a local JSON config file as the data source,
with a stub for future API integration.

Functions:
    is_earnings_imminent: Check if earnings are within N days for a symbol.
    load_earnings_dates: Load earnings dates from the config file.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Default path for earnings calendar JSON config
_DEFAULT_CONFIG = Path(__file__).parent.parent / "data" / "earnings_calendar.json"

# Default blocking window in days
_DEFAULT_WINDOW_DAYS = 5


def load_earnings_dates(
    config_path: Path | str | None = None,
) -> dict[str, list[str]]:
    """Load earnings dates from a JSON config file.

    The JSON file maps symbol -> list of date strings (YYYY-MM-DD).

    Args:
        config_path: Path to the JSON file. Defaults to
            ``data/earnings_calendar.json``.

    Returns:
        Dict mapping symbol to list of earnings date strings.
        Empty dict if file not found or invalid.
    """
    path = Path(config_path) if config_path else _DEFAULT_CONFIG
    if not path.exists():
        logger.debug("earnings_calendar: config not found at %s", path)
        return {}

    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("earnings_calendar: invalid format in %s", path)
            return {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("earnings_calendar: failed to load %s: %s", path, exc)
        return {}


def is_earnings_imminent(
    symbol: str,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    config_path: Path | str | None = None,
    reference_date: datetime | None = None,
) -> bool:
    """Check if a symbol has earnings within the blocking window.

    Args:
        symbol: Ticker symbol to check.
        window_days: Number of days before earnings to block signals.
        config_path: Optional override for the earnings config file path.
        reference_date: Date to check against (defaults to now).

    Returns:
        True if earnings are within ``window_days`` of the reference date.
    """
    earnings_map = load_earnings_dates(config_path)
    dates_str = earnings_map.get(symbol.upper(), [])

    if not dates_str:
        return False

    ref = reference_date or datetime.now()
    ref_date = ref.date() if hasattr(ref, "date") else ref

    for date_str in dates_str:
        try:
            earnings_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        days_until = (earnings_date - ref_date).days
        if 0 <= days_until <= window_days:
            logger.info(
                "earnings_calendar: %s earnings in %d days (%s) — blocking",
                symbol, days_until, date_str,
            )
            return True

    return False
