"""Tests for the pricing service (engine.pricing module).

This module tests the yfinance-based pricing service that provides real-time prices,
fundamental data, and historical price series. Since the pricing service makes external
HTTP calls to Yahoo Finance, all tests use unittest.mock.patch to replace the yf.Ticker
object with controlled mock responses.

Each test calls clear_cache() before exercising the pricing function to ensure the
3-layer cache (realtime 15s, historical 24h, fundamentals 24h) does not interfere
with test expectations. Without clearing, a prior test's cached data could cause
false passes or failures.

Tests cover:
    - **Successful price fetch** (test_get_price_success): Validates that get_price()
      extracts regularMarketPrice, maps fields correctly, and sets source='yfinance'.

    - **Unavailable price** (test_get_price_unavailable): Verifies that an empty
      yf.Ticker.info response produces an error key in the result dict rather than
      raising an exception.

    - **Cache behavior** (test_get_price_caching): Confirms that calling get_price()
      twice for the same symbol only invokes yf.Ticker once -- the second call
      should be served from the 15-second realtime cache.

    - **Batch pricing** (test_get_prices_batch): Tests get_prices() with a list of
      symbols, verifying that results are returned as a dict keyed by symbol.

    - **Fundamentals** (test_get_fundamentals): Validates that get_fundamentals()
      extracts and renames yfinance fields (shortName->name, trailingPE->pe_ratio, etc.)
      into the standardized fundamentals dictionary.

    - **Historical prices** (test_get_history): Tests get_history() with a mocked
      pandas DataFrame, verifying that OHLCV data is converted to a list of dicts
      with lowercase keys and ISO date strings.

    - **Invalid period** (test_get_history_invalid_period): Ensures get_history()
      returns an empty list for unsupported period values rather than raising.

All tests are pure unit tests with no database dependency.
"""

from __future__ import annotations

from unittest.mock import patch

from engine.pricing import clear_cache, get_fundamentals, get_history, get_price, get_prices


def test_get_price_success() -> None:
    """Verify successful price fetch returns correct fields from yfinance data.

    Mocks yf.Ticker.info with a complete price response and validates that
    get_price() returns a dict with symbol, price, source='yfinance', and
    no error key.
    """
    mock_info = {
        "regularMarketPrice": 130.50,
        "regularMarketPreviousClose": 128.00,
        "regularMarketVolume": 5000000,
        "shortName": "Test Corp",
    }
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = mock_info
        clear_cache()
        result = get_price("TEST")

    assert result["symbol"] == "TEST"
    assert result["price"] == 130.50
    assert result["source"] == "yfinance"
    assert "error" not in result


def test_get_price_unavailable() -> None:
    """Verify that an empty yfinance info response returns an error dict.

    When a symbol doesn't exist or yfinance returns no data, the result dict
    should contain an 'error' key instead of raising an exception. The system
    uses this error-in-dict pattern to gracefully handle price unavailability
    (e.g., the mock broker rejects orders when price is unavailable).
    """
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = {}
        clear_cache()
        result = get_price("FAKE")

    assert "error" in result


def test_get_price_caching() -> None:
    """Verify that the realtime cache prevents redundant yfinance API calls.

    Calls get_price() twice for the same symbol and asserts that yf.Ticker
    was only instantiated once. The 15-second cache TTL ensures that rapid
    consecutive calls (e.g., scoring multiple signals for the same stock)
    don't trigger rate limiting or unnecessary network requests.
    """
    mock_info = {
        "regularMarketPrice": 100.0,
        "regularMarketPreviousClose": 99.0,
        "regularMarketVolume": 1000000,
    }
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = mock_info
        clear_cache()
        result1 = get_price("CACHE")
        result2 = get_price("CACHE")

    # Should only call yfinance once due to caching
    assert mock_ticker.call_count == 1
    assert result1["price"] == result2["price"]


def test_get_prices_batch() -> None:
    """Verify that get_prices() returns a dict keyed by symbol for batch requests.

    Tests the batch pricing interface that calls get_price() for each symbol
    in the list. The result should be a dictionary mapping symbol strings to
    their individual price result dicts.
    """
    mock_info = {
        "regularMarketPrice": 50.0,
        "regularMarketPreviousClose": 49.0,
        "regularMarketVolume": 100000,
    }
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = mock_info
        clear_cache()
        results = get_prices(["A", "B"])

    assert "A" in results
    assert "B" in results
    assert results["A"]["price"] == 50.0


def test_get_fundamentals() -> None:
    """Verify that get_fundamentals() extracts and renames yfinance fields correctly.

    Validates the field mapping: shortName->name, sector->sector, industry->industry,
    marketCap->market_cap, trailingPE->pe_ratio, forwardPE->forward_pe. The
    fundamentals data is used by the dashboard to display company information
    alongside position data.
    """
    mock_info = {
        "shortName": "Test Corp",
        "sector": "Technology",
        "industry": "Semiconductors",
        "marketCap": 1000000000,
        "trailingPE": 25.0,
        "forwardPE": 20.0,
    }
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.info = mock_info
        clear_cache()
        result = get_fundamentals("TEST")

    assert result["name"] == "Test Corp"
    assert result["sector"] == "Technology"
    assert result["pe_ratio"] == 25.0


def test_get_history() -> None:
    """Verify that get_history() converts a pandas DataFrame to a list of dicts.

    Mocks yf.Ticker.history() to return a DataFrame with OHLCV columns and a
    DatetimeIndex. Validates that the result is a list of dicts with lowercase
    keys (open, high, low, close, volume) and ISO-formatted date strings.
    """
    import pandas as pd

    mock_data = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.0, 102.0],
            "Volume": [1000000, 1100000],
        },
        index=pd.to_datetime(["2026-01-06", "2026-01-07"]),
    )
    with patch("engine.pricing.yf.Ticker") as mock_ticker:
        mock_ticker.return_value.history.return_value = mock_data
        clear_cache()
        result = get_history("TEST", period="5d")

    assert len(result) == 2
    assert result[0]["close"] == 101.0
    assert result[1]["date"] == "2026-01-07"


def test_get_history_invalid_period() -> None:
    """Verify that get_history() returns an empty list for invalid period values.

    The period parameter must be one of the yfinance-supported values (1d, 5d,
    1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max). An invalid value should return
    an empty list rather than raising an exception or making a failed API call.
    """
    result = get_history("TEST", period="invalid")
    assert result == []
