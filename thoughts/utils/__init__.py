"""money_thoughts utilities for verified market data.

This package provides the core utility functions for the money_thoughts module,
which is the "thinking" half of the two-module investment system. All utilities
here deal with retrieving, storing, and analyzing real market data -- never
LLM-generated estimates.

The package exposes three categories of functionality:

1. **Price / Market Data** (from ``utils.price``):
   - ``get_price`` / ``get_prices`` -- real-time quotes via yfinance (Finnhub fallback)
   - ``get_fundamentals`` -- company financials and key ratios
   - ``get_news`` -- recent headlines for a ticker
   - ``get_history`` -- OHLCV history for a ticker

2. **Prediction Markets** (from ``utils.polymarket``):
   - ``search_markets`` -- find Polymarket events by keyword
   - ``get_market`` -- fetch a single market by ID
   - ``get_related_markets`` -- discover markets related to a stock ticker

3. **Investment Metrics** (from ``utils.metrics``):
   - ``calculate_win_rate`` -- win/loss ratio from acted ideas in history/ideas/
   - ``calculate_calibration`` -- conviction-vs-outcome calibration analysis
   - ``calculate_pass_accuracy`` -- were "pass" decisions correct in hindsight?
   - ``calculate_timeframe_accuracy`` -- stated-vs-actual holding period analysis
   - ``analyze_by_theme`` -- theme-level win rates (stub, not yet implemented)
   - ``bootstrap_metrics`` -- regenerate the metrics.md file from raw data

Additional modules not re-exported here but used internally:

- ``utils.config`` -- centralised environment variable loading and rate-limit constants
- ``utils.db`` -- SQLite persistence layer for prices, trades, and portfolio snapshots
- ``utils.charts`` -- Obsidian Charts-compatible YAML chart generation

Typical usage from a Claude Code skill or CLI session::

    from utils import get_price, get_fundamentals, search_markets
    price_data = get_price("AAPL")
    fundamentals = get_fundamentals("AAPL")
    markets = search_markets("Apple stock")
"""

from utils.metrics import (
    analyze_by_theme,
    bootstrap_metrics,
    calculate_calibration,
    calculate_pass_accuracy,
    calculate_timeframe_accuracy,
    calculate_win_rate,
)
from utils.polymarket import get_market, get_related_markets, search_markets
from utils.price import get_fundamentals, get_history, get_news, get_price, get_prices

__all__ = [
    "get_price",
    "get_prices",
    "get_fundamentals",
    "get_news",
    "get_history",
    "search_markets",
    "get_market",
    "get_related_markets",
    "calculate_win_rate",
    "calculate_calibration",
    "calculate_pass_accuracy",
    "calculate_timeframe_accuracy",
    "analyze_by_theme",
    "bootstrap_metrics",
]
