"""Market data -- yfinance with Finnhub fallback.

This module is the primary interface for retrieving live and historical market
data in the money_thoughts system. It wraps two data providers:

- **yfinance** (primary) -- Free, no API key required. Provides real-time
  quotes, fundamentals, news, and OHLCV history via Yahoo Finance.
- **Finnhub** (fallback) -- Used only when yfinance fails or returns no data
  *and* a ``FINNHUB_API_KEY`` is configured in the environment. Provides
  real-time quotes only (not fundamentals, news, or history).

Every function that hits an external API enforces a per-request delay via
``_rate_limit()`` to stay within Yahoo Finance's undocumented rate limits
(configured in ``utils.config.YFINANCE_DELAY``).

All returned data uses plain Python dicts rather than custom classes, making it
easy to serialise to JSON or insert into the SQLite database (``utils.db``).

Functions
---------
- ``get_price(symbol)`` -- Current quote with price, change, volume
- ``get_prices(symbols)`` -- Batch wrapper around ``get_price``
- ``get_fundamentals(symbol)`` -- Company financials and key ratios
- ``get_news(symbol, days)`` -- Recent headlines from Yahoo Finance
- ``get_history(symbol, period)`` -- OHLCV candles for a given period

This module is consumed by:
- CLI skills (``/pulse``, ``/research``, ``/refresh``, ``/discover``)
- ``utils.db.ensure_prices_current`` for backfilling stale price data
- ``utils.charts`` indirectly, since charts read from the DB which is
  populated by functions here

Network calls:
  Every public function makes at least one HTTP request to Yahoo Finance
  (and possibly one to Finnhub on fallback). Callers should be prepared for
  network-related delays of 1-5 seconds per call.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import yfinance as yf
from utils.config import FINNHUB_API_KEY, YFINANCE_DELAY

# Track last request time for rate limiting
_last_request_time = 0.0


def _rate_limit() -> None:
    """Enforce a minimum delay between consecutive yfinance requests.

    Uses the module-level ``_last_request_time`` to track when the previous
    request was made. If the elapsed time since that request is less than
    ``YFINANCE_DELAY`` (default 0.1 s), this function sleeps for the
    remaining duration before allowing the next request to proceed.

    This is a simple token-bucket-of-one approach. It is not thread-safe,
    but money_thoughts runs single-threaded so that is acceptable.

    Side effects:
        - Mutates the module-level ``_last_request_time`` global.
        - May call ``time.sleep()`` to enforce the delay.
    """
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < YFINANCE_DELAY:
        time.sleep(YFINANCE_DELAY - elapsed)
    _last_request_time = time.time()


def _get_finnhub_price(symbol: str) -> dict[str, Any] | None:
    """Attempt to fetch a real-time quote from Finnhub as a fallback.

    This function is only called when yfinance fails or returns no price data.
    It requires ``FINNHUB_API_KEY`` to be set in the environment (loaded via
    ``utils.config``). If the key is absent, returns ``None`` immediately
    without making any network call.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Passed directly to
            the Finnhub client without normalisation.

    Returns:
        A dict with keys ``symbol``, ``price``, ``change``, ``change_percent``,
        ``volume`` (always None from Finnhub), ``timestamp`` (UTC ISO format),
        and ``source`` (``"finnhub"``).

        Returns ``None`` if:
        - ``FINNHUB_API_KEY`` is not configured
        - The Finnhub API returns no data or a zero current price
        - Any exception occurs during the API call

    Side effects:
        - Makes one HTTP GET request to the Finnhub REST API.
        - Imports the ``finnhub`` package lazily (only when actually called).
    """
    if not FINNHUB_API_KEY:
        return None

    try:
        import finnhub

        client = finnhub.Client(api_key=FINNHUB_API_KEY)
        quote = client.quote(symbol)

        if quote and quote.get("c"):
            return {
                "symbol": symbol,
                "price": quote["c"],
                "change": quote["d"],
                "change_percent": quote["dp"],
                "volume": None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "finnhub",
            }
    except Exception:
        pass

    return None


def get_price(symbol: str) -> dict[str, Any]:
    """Get the current price and basic quote data for a single stock ticker.

    This is the primary real-time price function in money_thoughts. It first
    tries yfinance; if that fails or returns no price, it falls back to
    Finnhub (when configured). If both sources fail, it returns an error dict.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``, ``"MSFT"``). Will be
            upper-cased in the returned dict.

    Returns:
        On success, a dict with the following keys:
            - ``symbol`` (str): Upper-cased ticker
            - ``price`` (float): Current market price
            - ``change`` (float | None): Absolute price change from previous close
            - ``change_percent`` (float | None): Percentage change from previous close
            - ``volume`` (int | None): Current trading volume
            - ``timestamp`` (str): UTC ISO-8601 timestamp of the fetch
            - ``source`` (str): ``"yfinance"`` or ``"finnhub"``

        On failure, a dict with:
            - ``error`` (str): Description of the failure
            - ``symbol`` (str): The requested ticker

    Side effects:
        - Calls ``_rate_limit()`` which may sleep.
        - Makes one HTTP request to Yahoo Finance, and possibly one to Finnhub.
    """
    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or info.get("regularMarketPrice") is None:
            finnhub_data = _get_finnhub_price(symbol)
            if finnhub_data:
                return finnhub_data
            return {"error": "Price unavailable", "symbol": symbol}

        price = info.get("regularMarketPrice") or info.get("currentPrice")
        prev_close = info.get("regularMarketPreviousClose", 0)
        change = (price - prev_close) if price and prev_close else None
        change_pct = (change / prev_close * 100) if change and prev_close else None

        return {
            "symbol": symbol.upper(),
            "price": price,
            "change": round(change, 2) if change else None,
            "change_percent": round(change_pct, 2) if change_pct else None,
            "volume": info.get("regularMarketVolume"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance",
        }

    except Exception:
        finnhub_data = _get_finnhub_price(symbol)
        if finnhub_data:
            return finnhub_data
        return {"error": "Price unavailable", "symbol": symbol}


def get_prices(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Batch price lookup for multiple tickers.

    Iterates over the given symbols and calls ``get_price`` for each one
    sequentially (with rate limiting between calls). This is a convenience
    wrapper -- there is no batch API call under the hood.

    Parameters:
        symbols: List of stock ticker symbols (e.g. ``["AAPL", "MSFT", "GOOG"]``).

    Returns:
        A dict mapping each symbol to its ``get_price`` result dict. Keys match
        the input symbols exactly (not upper-cased); the inner dicts follow the
        same format as ``get_price``.

    Side effects:
        - Makes one HTTP request per symbol (with rate-limit delays).
    """
    return {symbol: get_price(symbol) for symbol in symbols}


def get_fundamentals(symbol: str) -> dict[str, Any]:
    """Get company fundamentals and key financial metrics for a stock ticker.

    Retrieves a broad set of valuation, profitability, and balance-sheet
    metrics from Yahoo Finance. Useful for the ``/research`` skill when
    evaluating whether a ticker aligns with a thesis.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``).

    Returns:
        On success, a dict with the following keys (any may be ``None`` if
        Yahoo Finance does not have the data):
            - ``symbol`` (str): Upper-cased ticker
            - ``name`` (str): Company short name
            - ``sector`` (str): GICS sector
            - ``industry`` (str): GICS industry
            - ``market_cap`` (int): Market capitalisation in USD
            - ``pe_ratio`` (float): Trailing P/E ratio
            - ``forward_pe`` (float): Forward P/E ratio
            - ``peg_ratio`` (float): PEG ratio
            - ``price_to_book`` (float): Price-to-book ratio
            - ``dividend_yield`` (float): Dividend yield as a decimal (0.02 = 2%)
            - ``revenue`` (int): Total revenue in USD
            - ``revenue_growth`` (float): Year-over-year revenue growth as decimal
            - ``profit_margin`` (float): Net profit margin as decimal
            - ``operating_margin`` (float): Operating margin as decimal
            - ``debt_to_equity`` (float): Debt-to-equity ratio
            - ``current_ratio`` (float): Current ratio
            - ``52_week_high`` (float): 52-week high price
            - ``52_week_low`` (float): 52-week low price
            - ``avg_volume`` (int): Average daily trading volume
            - ``source`` (str): Always ``"yfinance"``

        On failure, a dict with:
            - ``error`` (str): Description of the failure
            - ``symbol`` (str): The requested ticker

    Side effects:
        - Calls ``_rate_limit()`` which may sleep.
        - Makes one HTTP request to Yahoo Finance.
    """
    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        if not info or not info.get("shortName"):
            return {"error": "Fundamentals unavailable", "symbol": symbol}

        return {
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

    except Exception:
        return {"error": "Fundamentals unavailable", "symbol": symbol}


def get_news(symbol: str, days: int = 7) -> list[dict[str, Any]]:
    """Get recent news headlines for a stock ticker from Yahoo Finance.

    Fetches the news feed attached to the yfinance Ticker object and filters
    articles to only those published within the specified lookback window.
    Used by the ``/research`` skill to provide current-event context when
    evaluating a ticker.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``).
        days: Number of days to look back from now. Articles older than this
            cutoff are excluded. Defaults to 7 (one week).

    Returns:
        A list of dicts, each with:
            - ``title`` (str): Headline text
            - ``publisher`` (str): Source publisher name
            - ``url`` (str): Link to the full article
            - ``published`` (str): UTC ISO-8601 timestamp of publication
            - ``summary`` (str | None): Short summary if provided by Yahoo

        Returns an empty list if:
        - The ticker has no news
        - All articles are older than the ``days`` cutoff
        - Any exception occurs during the fetch

    Side effects:
        - Calls ``_rate_limit()`` which may sleep.
        - Makes one HTTP request to Yahoo Finance.
    """
    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news

        if not news:
            return []

        cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
        results: list[dict[str, Any]] = []

        for article in news:
            pub_time = article.get("providerPublishTime", 0)
            if pub_time >= cutoff:
                results.append(
                    {
                        "title": article.get("title"),
                        "publisher": article.get("publisher"),
                        "url": article.get("link"),
                        "published": datetime.fromtimestamp(pub_time, tz=timezone.utc).isoformat(),
                        "summary": article.get("summary"),
                    }
                )

        return results

    except Exception:
        return []


def get_history(symbol: str, period: str = "1mo") -> list[dict[str, Any]]:
    """Get historical OHLCV price data for a stock ticker.

    Fetches candlestick data from Yahoo Finance for the specified lookback
    period. Used by the ``/research`` skill for trend analysis and by
    ``utils.db.backfill_prices`` indirectly (which uses yfinance directly).

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``).
        period: Time period string accepted by yfinance. Valid values are:
            ``"1d"``, ``"5d"``, ``"1mo"``, ``"3mo"``, ``"6mo"``, ``"1y"``,
            ``"2y"``, ``"5y"``, ``"ytd"``, ``"max"``.
            Defaults to ``"1mo"`` (one month).
            If an invalid period is provided, returns an empty list immediately
            without making any network request.

    Returns:
        A list of dicts, each representing one trading day with:
            - ``date`` (str): Date in ``YYYY-MM-DD`` format
            - ``open`` (float): Opening price, rounded to 2 decimal places
            - ``high`` (float): Intraday high, rounded to 2 decimal places
            - ``low`` (float): Intraday low, rounded to 2 decimal places
            - ``close`` (float): Closing price, rounded to 2 decimal places
            - ``volume`` (int): Trading volume

        Returns an empty list if:
        - ``period`` is not in the valid set
        - The ticker returns no history (e.g. invalid symbol)
        - Any exception occurs during the fetch

    Side effects:
        - Calls ``_rate_limit()`` which may sleep.
        - Makes one HTTP request to Yahoo Finance.
    """
    valid_periods = {"1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"}
    if period not in valid_periods:
        return []

    _rate_limit()

    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period=period)

        if hist.empty:
            return []

        results: list[dict[str, Any]] = []
        for date_idx, row in hist.iterrows():
            results.append(
                {
                    "date": date_idx.strftime("%Y-%m-%d"),
                    "open": round(row["Open"], 2),
                    "high": round(row["High"], 2),
                    "low": round(row["Low"], 2),
                    "close": round(row["Close"], 2),
                    "volume": int(row["Volume"]),
                }
            )

        return results

    except Exception:
        return []
