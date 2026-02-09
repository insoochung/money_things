"""Earnings calendar blocking: prevents signals near earnings dates.

Checks whether a symbol has an upcoming earnings report within a configurable
window (default 5 days). Uses a local JSON config file as primary source,
with yfinance API as fallback for symbols not in the static file.

Functions:
    is_earnings_imminent: Check if earnings are within N days for a symbol.
    load_earnings_dates: Load earnings dates from the config file.
    fetch_earnings_date: Fetch next earnings date from yfinance.
    get_next_earnings: Get next earnings date (static + API fallback).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime, date as date_type
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default path for earnings calendar JSON config
_DEFAULT_CONFIG = Path(__file__).parent.parent / "data" / "earnings_calendar.json"

# Default blocking window in days
_DEFAULT_WINDOW_DAYS = 5

# Cache for yfinance earnings lookups: {symbol: (earnings_date, fetch_time)}
_earnings_cache: dict[str, tuple[date_type | None, float]] = {}
_CACHE_TTL = 86400.0  # 24 hours


def clear_cache() -> None:
    """Clear the earnings date cache."""
    _earnings_cache.clear()


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


def fetch_earnings_date(symbol: str) -> date_type | None:
    """Fetch the next earnings date for a symbol from yfinance.

    Results are cached for 24 hours to minimize API calls.

    Args:
        symbol: Ticker symbol to look up.

    Returns:
        Next earnings date or None if unavailable.
    """
    now = time.time()
    cached = _earnings_cache.get(symbol.upper())
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        # yfinance exposes earnings_dates as a DataFrame or calendar
        cal = ticker.calendar
        if cal is not None and not (hasattr(cal, "empty") and cal.empty):
            # calendar can be a dict or DataFrame
            if isinstance(cal, dict):
                earnings_date_val = cal.get("Earnings Date")
                if earnings_date_val:
                    if isinstance(earnings_date_val, list) and earnings_date_val:
                        earnings_date_val = earnings_date_val[0]
                    if hasattr(earnings_date_val, "date"):
                        result = earnings_date_val.date()
                    elif isinstance(earnings_date_val, str):
                        result = datetime.strptime(earnings_date_val[:10], "%Y-%m-%d").date()
                    else:
                        result = None
                    _earnings_cache[symbol.upper()] = (result, now)
                    return result
            else:
                # DataFrame format — try to extract Earnings Date
                if "Earnings Date" in cal.index:
                    val = cal.loc["Earnings Date"]
                    if hasattr(val, "iloc"):
                        val = val.iloc[0]
                    if hasattr(val, "date"):
                        result = val.date()
                    elif isinstance(val, str):
                        result = datetime.strptime(val[:10], "%Y-%m-%d").date()
                    else:
                        result = None
                    _earnings_cache[symbol.upper()] = (result, now)
                    return result

    except Exception as e:
        logger.debug("earnings_calendar: yfinance fetch failed for %s: %s", symbol, e)

    _earnings_cache[symbol.upper()] = (None, now)
    return None


def get_next_earnings(
    symbol: str,
    *,
    config_path: Path | str | None = None,
    reference_date: datetime | None = None,
    use_api: bool = True,
) -> date_type | None:
    """Get the next earnings date for a symbol.

    Checks static JSON file first, falls back to yfinance API.

    Args:
        symbol: Ticker symbol.
        config_path: Override path for static earnings file.
        reference_date: Reference date (defaults to now).
        use_api: Whether to try yfinance as fallback.

    Returns:
        Next upcoming earnings date, or None.
    """
    ref = reference_date or datetime.now()
    ref_d = ref.date() if hasattr(ref, "date") else ref

    # Check static file first
    earnings_map = load_earnings_dates(config_path)
    dates_str = earnings_map.get(symbol.upper(), [])

    for date_str in dates_str:
        try:
            ed = datetime.strptime(date_str, "%Y-%m-%d").date()
            if ed >= ref_d:
                return ed
        except ValueError:
            continue

    # Fallback to yfinance
    if use_api:
        return fetch_earnings_date(symbol)

    return None


def is_earnings_imminent(
    symbol: str,
    *,
    window_days: int = _DEFAULT_WINDOW_DAYS,
    config_path: Path | str | None = None,
    reference_date: datetime | None = None,
    use_api: bool = True,
) -> bool:
    """Check if a symbol has earnings within the blocking window.

    Checks static JSON first, then yfinance API as fallback.

    Args:
        symbol: Ticker symbol to check.
        window_days: Number of days before earnings to block signals.
        config_path: Optional override for the earnings config file path.
        reference_date: Date to check against (defaults to now).
        use_api: Whether to try yfinance for symbols not in static file.

    Returns:
        True if earnings are within ``window_days`` of the reference date.
    """
    ref = reference_date or datetime.now()
    ref_d = ref.date() if hasattr(ref, "date") else ref

    next_earnings = get_next_earnings(
        symbol,
        config_path=config_path,
        reference_date=reference_date,
        use_api=use_api,
    )

    if next_earnings is None:
        return False

    days_until = (next_earnings - ref_d).days
    if 0 <= days_until <= window_days:
        logger.info(
            "earnings_calendar: %s earnings in %d days (%s) — blocking",
            symbol, days_until, next_earnings.isoformat(),
        )
        return True

    return False
