"""Benchmark data fetching and portfolio analytics calculations.

Provides cached yfinance data retrieval and functions to compute
alpha, beta, correlation, tracking error, capture ratios, and
period returns from portfolio NAV vs benchmark time series.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Cache directory for yfinance data (24h TTL)
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_TTL_SECONDS = 86400  # 24 hours


def _cache_path(key: str) -> Path:
    """Return filesystem cache path for a given key."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    h = hashlib.md5(key.encode()).hexdigest()
    return CACHE_DIR / f"yf_{h}.json"


def _read_cache(key: str) -> list[dict[str, Any]] | None:
    """Read cached data if it exists and is not expired."""
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        if time.time() - data.get("ts", 0) < CACHE_TTL_SECONDS:
            return data["rows"]
    except Exception:
        pass
    return None


def _write_cache(key: str, rows: list[dict[str, Any]]) -> None:
    """Write data to cache."""
    p = _cache_path(key)
    try:
        p.write_text(json.dumps({"ts": time.time(), "rows": rows}))
    except Exception as exc:
        logger.warning("Cache write failed: %s", exc)


def fetch_benchmark_prices(
    symbol: str, start_date: str, end_date: str
) -> list[dict[str, Any]]:
    """Fetch daily close prices for a benchmark symbol via yfinance.

    Args:
        symbol: Benchmark ticker (e.g. 'SPY').
        start_date: Start date YYYY-MM-DD.
        end_date: End date YYYY-MM-DD.

    Returns:
        List of dicts with 'date' (str) and 'close' (float) keys,
        sorted ascending by date.
    """
    cache_key = f"{symbol}_{start_date}_{end_date}"
    cached = _read_cache(cache_key)
    if cached is not None:
        return cached

    import yfinance as yf

    ticker = yf.Ticker(symbol)
    df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

    if df.empty:
        logger.warning("No yfinance data for %s %s-%s", symbol, start_date, end_date)
        return []

    rows: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        rows.append({
            "date": idx.strftime("%Y-%m-%d"),
            "close": float(row["Close"]),
        })

    _write_cache(cache_key, rows)
    return rows


def align_series(
    portfolio_data: list[dict[str, Any]],
    benchmark_data: list[dict[str, Any]],
) -> tuple[list[float], list[float], list[str]]:
    """Align portfolio and benchmark data on common dates.

    Args:
        portfolio_data: Portfolio rows with 'date' and 'total_value'.
        benchmark_data: Benchmark rows with 'date' and 'close'.

    Returns:
        Tuple of (portfolio_values, benchmark_values, dates) aligned
        on common dates.
    """
    bm_map = {r["date"]: r["close"] for r in benchmark_data}
    pf_vals: list[float] = []
    bm_vals: list[float] = []
    dates: list[str] = []
    for pv in portfolio_data:
        d = pv["date"]
        if d in bm_map:
            pf_vals.append(pv["total_value"])
            bm_vals.append(bm_map[d])
            dates.append(d)
    return pf_vals, bm_vals, dates


def daily_returns(values: list[float]) -> np.ndarray:
    """Compute daily percentage returns from a price series.

    Args:
        values: List of prices/values.

    Returns:
        Numpy array of daily returns (length = len(values) - 1).
    """
    arr = np.array(values)
    return np.diff(arr) / arr[:-1] * 100


def compute_benchmark_stats(
    pf_returns: np.ndarray, bm_returns: np.ndarray
) -> dict[str, float]:
    """Compute alpha, beta, correlation, tracking error, capture ratios.

    Args:
        pf_returns: Portfolio daily returns (%).
        bm_returns: Benchmark daily returns (%).

    Returns:
        Dict with keys: alpha_pct, beta, correlation,
        tracking_error_pct, information_ratio,
        up_capture_pct, down_capture_pct.
    """
    if len(pf_returns) < 2 or len(bm_returns) < 2:
        return _empty_stats()

    # Beta and alpha via OLS
    cov = np.cov(pf_returns, bm_returns)
    beta = float(cov[0, 1] / cov[1, 1]) if cov[1, 1] != 0 else 0.0
    alpha_daily = float(np.mean(pf_returns) - beta * np.mean(bm_returns))
    alpha_pct = alpha_daily * 252  # annualize

    # Correlation
    corr_matrix = np.corrcoef(pf_returns, bm_returns)
    correlation = float(corr_matrix[0, 1])

    # Tracking error (annualized)
    excess = pf_returns - bm_returns
    tracking_error_pct = float(np.std(excess, ddof=1) * np.sqrt(252))

    # Information ratio
    info_ratio = (
        float(np.mean(excess) * np.sqrt(252) / np.std(excess, ddof=1))
        if np.std(excess, ddof=1) > 0
        else 0.0
    )

    # Up/down capture
    up_mask = bm_returns > 0
    down_mask = bm_returns < 0
    up_capture = (
        float(np.mean(pf_returns[up_mask]) / np.mean(bm_returns[up_mask]) * 100)
        if np.any(up_mask) and np.mean(bm_returns[up_mask]) != 0
        else 100.0
    )
    down_capture = (
        float(np.mean(pf_returns[down_mask]) / np.mean(bm_returns[down_mask]) * 100)
        if np.any(down_mask) and np.mean(bm_returns[down_mask]) != 0
        else 100.0
    )

    return {
        "alpha_pct": round(alpha_pct, 4),
        "beta": round(beta, 4),
        "correlation": round(correlation, 4),
        "tracking_error_pct": round(tracking_error_pct, 4),
        "information_ratio": round(info_ratio, 4),
        "up_capture_pct": round(up_capture, 2),
        "down_capture_pct": round(down_capture, 2),
    }


def _empty_stats() -> dict[str, float]:
    """Return zeroed-out benchmark stats."""
    return {
        "alpha_pct": 0.0,
        "beta": 0.0,
        "correlation": 0.0,
        "tracking_error_pct": 0.0,
        "information_ratio": 0.0,
        "up_capture_pct": 0.0,
        "down_capture_pct": 0.0,
    }


def calculate_period_return(
    portfolio_data: list[dict[str, Any]], ref_date: str
) -> float:
    """Calculate return from ref_date to latest NAV.

    Args:
        portfolio_data: Sorted portfolio rows with 'date', 'total_value'.
        ref_date: Reference date string YYYY-MM-DD.

    Returns:
        Return percentage, or 0.0 if insufficient data.
    """
    if not portfolio_data:
        return 0.0

    end_value = portfolio_data[-1]["total_value"]

    # Find the closest date >= ref_date
    for pv in portfolio_data:
        if pv["date"] >= ref_date:
            start_value = pv["total_value"]
            if start_value > 0:
                return (end_value - start_value) / start_value * 100
            return 0.0
    return 0.0


def period_start_date(period: str, today: date | None = None) -> str:
    """Get start date string for YTD, MTD, WTD periods.

    Args:
        period: One of 'ytd', 'mtd', 'wtd'.
        today: Override for today's date (for testing).

    Returns:
        Date string YYYY-MM-DD.
    """
    today = today or date.today()
    if period == "ytd":
        return f"{today.year}-01-01"
    elif period == "mtd":
        return today.replace(day=1).isoformat()
    elif period == "wtd":
        # Monday of current week
        monday = today - timedelta(days=today.weekday())
        return monday.isoformat()
    return today.isoformat()
