"""Price service for fetching real-time and historical market data via yfinance.

This module provides the pricing layer for the money_moves system. It fetches
real-time prices, historical OHLCV data, and company fundamentals from Yahoo Finance
using the yfinance library. All price data in money_moves comes from this module --
prices are NEVER estimated by LLMs, ensuring accuracy and auditability.

The module implements three layers of caching to minimize API calls and stay within
rate limits:
    - Real-time price cache (_price_cache): 15-second TTL for current prices
    - Historical data cache (_history_cache): 24-hour TTL for OHLCV history
    - Fundamentals cache (_fundamentals_cache): 24-hour TTL for company metrics

Rate limiting is enforced via _rate_limit() which ensures a minimum delay between
consecutive yfinance API calls (default 1.0 second, configurable via set_request_delay).

When a Database instance is provided, fetched prices are also persisted to the
price_history table for offline analysis, charting, and historical lookback. Database
writes use INSERT OR IGNORE to avoid duplicate entries.

This module is used by:
    - broker.mock.MockBroker: Gets fill prices for simulated trades
    - The dashboard API: Provides real-time price data for the web frontend
    - engine.signals.SignalEngine: Price data for funding plan generation

Functions:
    get_price: Fetch current price for a single symbol
    get_prices: Batch fetch current prices for multiple symbols
    get_fundamentals: Fetch company fundamentals and valuation metrics
    get_history: Fetch historical OHLCV price data
    clear_cache: Clear all caches (used in tests)
    set_request_delay: Configure rate limiting delay
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

import yfinance as yf

from db.database import Database

logger = logging.getLogger(__name__)

# Server-side cache: {symbol: (price_data, fetch_time)}
_price_cache: dict[str, tuple[dict[str, Any], float]] = {}
_history_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}
_fundamentals_cache: dict[str, tuple[dict[str, Any], float]] = {}

REALTIME_TTL = 15.0  # 15 seconds
HISTORICAL_TTL = 86400.0  # 1 day
FUNDAMENTALS_TTL = 86400.0  # 1 day

_last_request_time = 0.0
_request_delay = 1.0


def set_request_delay(delay: float) -> None:
    """Set the minimum delay between consecutive yfinance API requests.

    Used to configure rate limiting. In tests, this is set to 0 to avoid
    unnecessary delays. In production, defaults to 1.0 second to stay within
    Yahoo Finance's rate limits.

    Args:
        delay: Minimum delay in seconds between API requests. Set to 0 for tests.

    Side effects:
        Modifies the module-level _request_delay global variable.
    """
    global _request_delay
    _request_delay = delay


def _rate_limit() -> None:
    """Enforce minimum delay between consecutive yfinance API requests.

    Sleeps if the time since the last request is less than _request_delay seconds.
    This prevents hitting Yahoo Finance rate limits, which can result in temporary
    IP bans or degraded responses.

    Side effects:
        - May sleep the current thread for up to _request_delay seconds.
        - Updates the module-level _last_request_time global variable.
    """
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _request_delay:
        time.sleep(_request_delay - elapsed)
    _last_request_time = time.time()


def clear_cache() -> None:
    """Clear all in-memory price, history, and fundamentals caches.

    Used in tests to ensure each test gets fresh data from the (mocked) yfinance API
    rather than stale cached results. Also useful in production if a manual cache
    invalidation is needed.

    Side effects:
        Clears the module-level _price_cache, _history_cache, and _fundamentals_cache
        dictionaries.
    """
    _price_cache.clear()
    _history_cache.clear()
    _fundamentals_cache.clear()


def get_price(symbol: str, db: Database | None = None) -> dict[str, Any]:
    """Fetch the current price for a single stock symbol.

    Returns a dictionary with real-time price data from Yahoo Finance. Results are
    cached for 15 seconds to minimize API calls during rapid successive lookups
    (e.g., dashboard refresh, multiple signals for the same symbol).

    If a Database instance is provided, the price is also persisted to the
    price_history table with a '1m' interval for historical tracking.

    Args:
        symbol: Stock ticker symbol (e.g., 'NVDA', 'META', 'AAPL').
        db: Optional Database instance. If provided, the fetched price is written
            to the price_history table via INSERT OR IGNORE.

    Returns:
        Dictionary with keys:
            - symbol (str): Uppercased ticker symbol
            - price (float): Current market price
            - change (float | None): Dollar change from previous close
            - change_percent (float | None): Percentage change from previous close
            - volume (int | None): Current trading volume
            - timestamp (str): ISO 8601 timestamp of when the price was fetched
            - source (str): Always 'yfinance'
        On error, returns:
            - error (str): Error message
            - symbol (str): The requested symbol

    Side effects:
        - Network call to Yahoo Finance API (unless cached).
        - May sleep due to rate limiting.
        - If db is provided, writes to price_history table.
    """
    now = time.time()
    cached = _price_cache.get(symbol)
    if cached and (now - cached[1]) < REALTIME_TTL:
        return cached[0]

    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or info.get("regularMarketPrice") is None:
            return {"error": "Price unavailable", "symbol": symbol}

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("regularMarketPreviousClose", 0)
        change = (price - prev_close) if price and prev_close else None
        change_pct = (change / prev_close * 100) if change and prev_close else None

        result = {
            "symbol": symbol.upper(),
            "price": price,
            "change": round(change, 2) if change is not None else None,
            "change_percent": round(change_pct, 2) if change_pct is not None else None,
            "volume": info.get("regularMarketVolume"),
            "timestamp": datetime.now(UTC).isoformat(),
            "source": "yfinance",
        }

        _price_cache[symbol] = (result, now)

        # Store to price_history if db provided
        if db and price:
            try:
                db.execute(
                    """INSERT OR IGNORE INTO price_history
                       (symbol, timestamp, interval, close, volume)
                       VALUES (?, ?, '1m', ?, ?)""",
                    (
                        symbol.upper(),
                        result["timestamp"],
                        price,
                        info.get("regularMarketVolume"),
                    ),
                )
                db.connect().commit()
            except Exception:
                pass

        return result

    except Exception as e:
        logger.warning("Price fetch failed for %s: %s", symbol, e)
        return {"error": "Price unavailable", "symbol": symbol}


def get_prices(symbols: list[str], db: Database | None = None) -> dict[str, dict[str, Any]]:
    """Fetch current prices for multiple symbols in a batch.

    Iterates over the symbol list and calls get_price() for each. Results are
    returned as a dictionary keyed by symbol. Each call benefits from the 15-second
    cache, so repeated calls within the TTL window are essentially free.

    Args:
        symbols: List of stock ticker symbols to fetch prices for.
        db: Optional Database instance passed through to get_price() for
            price_history persistence.

    Returns:
        Dictionary mapping each symbol to its price data dictionary (same format
        as get_price() return value). Symbols that fail will have an 'error' key
        in their dictionary.

    Side effects:
        Same as get_price() for each symbol (network calls, rate limiting, DB writes).
    """
    return {symbol: get_price(symbol, db=db) for symbol in symbols}


def get_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch company fundamentals and key financial metrics.

    Returns a comprehensive set of fundamental data including valuation ratios,
    financial performance metrics, and market data. Results are cached for 24 hours
    since fundamentals change infrequently (typically updated quarterly).

    This data is used in the dashboard for position context and in future signal
    generation for fundamental-based analysis.

    Args:
        symbol: Stock ticker symbol (e.g., 'NVDA', 'META').

    Returns:
        Dictionary with keys:
            - symbol (str): Uppercased ticker symbol
            - name (str): Company short name
            - sector (str): Market sector (e.g., 'Technology')
            - industry (str): Specific industry (e.g., 'Semiconductors')
            - market_cap (int): Market capitalization in dollars
            - pe_ratio (float): Trailing P/E ratio
            - forward_pe (float): Forward P/E ratio
            - peg_ratio (float): PEG ratio
            - price_to_book (float): Price-to-book ratio
            - dividend_yield (float): Annual dividend yield
            - revenue (int): Total revenue
            - revenue_growth (float): Year-over-year revenue growth rate
            - profit_margin (float): Net profit margin
            - operating_margin (float): Operating margin
            - debt_to_equity (float): Debt-to-equity ratio
            - current_ratio (float): Current ratio
            - 52_week_high (float): 52-week high price
            - 52_week_low (float): 52-week low price
            - avg_volume (int): Average daily trading volume
            - source (str): Always 'yfinance'
        On error, returns:
            - error (str): Error message
            - symbol (str): The requested symbol

    Side effects:
        - Network call to Yahoo Finance API (unless cached within 24h).
        - May sleep due to rate limiting.
    """
    now = time.time()
    cached = _fundamentals_cache.get(symbol)
    if cached and (now - cached[1]) < FUNDAMENTALS_TTL:
        return cached[0]

    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or not info.get("shortName"):
            return {"error": "Fundamentals unavailable", "symbol": symbol}

        result = {
            "symbol": symbol.upper(),
            "name": info.get("shortName") or info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "pe_ratio": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "peg_ratio": info.get("pegRatio"),
            "price_to_book": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "revenue": info.get("totalRevenue"),
            "revenue_growth": info.get("revenueGrowth"),
            "profit_margin": info.get("profitMargins"),
            "operating_margin": info.get("operatingMargins"),
            "debt_to_equity": info.get("debtToEquity"),
            "current_ratio": info.get("currentRatio"),
            "52_week_high": info.get("fiftyTwoWeekHigh"),
            "52_week_low": info.get("fiftyTwoWeekLow"),
            "avg_volume": info.get("averageVolume"),
            "source": "yfinance",
        }

        _fundamentals_cache[symbol] = (result, now)
        return result

    except Exception:
        return {"error": "Fundamentals unavailable", "symbol": symbol}


def get_history(
    symbol: str, period: str = "1mo", db: Database | None = None
) -> list[dict[str, Any]]:
    """Fetch historical OHLCV (Open/High/Low/Close/Volume) price data.

    Returns a list of daily price bars for the specified time period. Results are
    cached for 24 hours. Valid period strings are validated against yfinance's
    supported periods before making the API call.

    If a Database instance is provided, all fetched history rows are bulk-inserted
    into the price_history table with a '1d' interval, using INSERT OR IGNORE to
    skip duplicates.

    Args:
        symbol: Stock ticker symbol (e.g., 'NVDA', 'META').
        period: Time period for historical data. Valid values:
            '1d', '5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', 'ytd', 'max'.
            Defaults to '1mo'. Invalid periods return an empty list.
        db: Optional Database instance. If provided, fetched history is written
            to the price_history table via bulk INSERT OR IGNORE.

    Returns:
        List of dictionaries, each containing:
            - date (str): Date in 'YYYY-MM-DD' format
            - open (float): Opening price (rounded to 2 decimal places)
            - high (float): High price (rounded to 2 decimal places)
            - low (float): Low price (rounded to 2 decimal places)
            - close (float): Closing price (rounded to 2 decimal places)
            - volume (int): Trading volume
        Returns empty list on error or invalid period.

    Side effects:
        - Network call to Yahoo Finance API (unless cached within 24h).
        - May sleep due to rate limiting.
        - If db is provided, bulk writes to price_history table.
    """
    valid_periods = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"}
    if period not in valid_periods:
        return []

    cache_key = f"{symbol}:{period}"
    now = time.time()
    cached = _history_cache.get(cache_key)
    if cached and (now - cached[1]) < HISTORICAL_TTL:
        return cached[0]

    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)

        if hist.empty:
            return []

        results = []
        for date, row in hist.iterrows():
            results.append(
                {
                    "date": date.strftime("%Y-%m-%d"),
                    "open": round(row["Open"], 2),
                    "high": round(row["High"], 2),
                    "low": round(row["Low"], 2),
                    "close": round(row["Close"], 2),
                    "volume": int(row["Volume"]),
                }
            )

        _history_cache[cache_key] = (results, now)

        # Store to price_history if db provided
        if db and results:
            rows = [
                (
                    symbol.upper(),
                    r["date"],
                    "1d",
                    r["open"],
                    r["high"],
                    r["low"],
                    r["close"],
                    r["volume"],
                )
                for r in results
            ]
            try:
                db.executemany(
                    """INSERT OR IGNORE INTO price_history
                       (symbol, timestamp, interval, open, high, low, close, volume)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    rows,
                )
                db.connect().commit()
            except Exception:
                pass

        return results

    except Exception:
        return []
