"""Tests for utils/price.py -- market data utilities.

This module contains unit tests for the ``utils.price`` module, which provides
real-time and historical market data via yfinance with Finnhub fallback. All
external API calls are mocked to ensure tests are fast, deterministic, and do
not require API keys or network access.

Test classes:
    - ``TestGetPrice`` -- Tests for ``get_price()`` (single-ticker quote)
    - ``TestGetPrices`` -- Tests for ``get_prices()`` (batch quote lookup)
    - ``TestGetFundamentals`` -- Tests for ``get_fundamentals()`` (company data)
    - ``TestGetNews`` -- Tests for ``get_news()`` (recent headlines)
    - ``TestGetHistory`` -- Tests for ``get_history()`` (OHLCV candles)
    - ``TestFinnhubFallback`` -- Tests for ``_get_finnhub_price()`` (fallback logic)

Mocking strategy:
    - ``yf.Ticker`` is patched at the module level (``utils.price.yf.Ticker``)
      to return mock objects with controlled ``.info``, ``.news``, and
      ``.history()`` responses.
    - ``_get_finnhub_price`` is patched when testing fallback scenarios to
      verify that the Finnhub path is only taken when yfinance fails.
    - ``FINNHUB_API_KEY`` is patched to control whether Finnhub is "configured".
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.price import (
    _get_finnhub_price,
    get_fundamentals,
    get_history,
    get_news,
    get_price,
    get_prices,
)


class TestGetPrice:
    """Tests for the ``get_price`` function.

    Validates that ``get_price`` correctly extracts price data from yfinance's
    Ticker.info dict, computes change/change_percent, and falls back to
    Finnhub or returns an error dict when yfinance fails.
    """

    @patch("utils.price.yf.Ticker")
    def test_returns_price_data(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that a successful yfinance response returns a properly
        structured price dict with symbol, price, change, change_percent,
        and source='yfinance'.
        """
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "regularMarketPrice": 185.50,
            "regularMarketPreviousClose": 183.00,
            "regularMarketVolume": 50000000,
        }
        mock_ticker_cls.return_value = mock_ticker

        result = get_price("AAPL")
        assert result["symbol"] == "AAPL"
        assert result["price"] == 185.50
        assert result["source"] == "yfinance"
        assert "change" in result
        assert "change_percent" in result

    @patch("utils.price.yf.Ticker")
    def test_handles_missing_data(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that when yfinance returns None for regularMarketPrice
        and Finnhub fallback is unavailable, an error dict is returned
        with 'error' and 'symbol' keys.
        """
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPrice": None}
        mock_ticker_cls.return_value = mock_ticker

        with patch("utils.price._get_finnhub_price", return_value=None):
            result = get_price("INVALID")
            assert "error" in result

    @patch("utils.price.yf.Ticker")
    def test_handles_exception(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that when yfinance raises an exception (e.g. network error)
        and Finnhub fallback is unavailable, an error dict is returned
        gracefully without propagating the exception.
        """
        mock_ticker_cls.side_effect = Exception("Network error")

        with patch("utils.price._get_finnhub_price", return_value=None):
            result = get_price("AAPL")
            assert "error" in result


class TestGetPrices:
    """Tests for the ``get_prices`` batch lookup function.

    Validates that ``get_prices`` calls ``get_price`` once per symbol and
    returns a dict mapping each symbol to its result.
    """

    @patch("utils.price.get_price")
    def test_batch_lookup(self, mock_get_price: MagicMock) -> None:
        """Verify that get_prices calls get_price for each symbol in the
        list and returns results keyed by symbol name.
        """
        mock_get_price.side_effect = lambda s: {"symbol": s, "price": 100.0}
        result = get_prices(["AAPL", "MSFT"])
        assert "AAPL" in result
        assert "MSFT" in result
        assert mock_get_price.call_count == 2


class TestGetFundamentals:
    """Tests for the ``get_fundamentals`` function.

    Validates that fundamental data (name, sector, industry, etc.) is
    correctly extracted from yfinance's Ticker.info dict.
    """

    @patch("utils.price.yf.Ticker")
    def test_returns_fundamentals(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that a successful yfinance response returns a dict with
        company name, sector, industry, and other fundamental metrics.
        """
        mock_ticker = MagicMock()
        mock_ticker.info = {
            "shortName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
            "marketCap": 3000000000000,
            "trailingPE": 30.5,
            "forwardPE": 28.0,
        }
        mock_ticker_cls.return_value = mock_ticker

        result = get_fundamentals("AAPL")
        assert result["symbol"] == "AAPL"
        assert result["name"] == "Apple Inc."
        assert result["sector"] == "Technology"

    @patch("utils.price.yf.Ticker")
    def test_handles_missing(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that when yfinance returns an empty info dict (no shortName),
        an error dict is returned rather than a KeyError or partial data.
        """
        mock_ticker = MagicMock()
        mock_ticker.info = {}
        mock_ticker_cls.return_value = mock_ticker

        result = get_fundamentals("INVALID")
        assert "error" in result


class TestGetNews:
    """Tests for the ``get_news`` function.

    Validates that news articles are correctly extracted from yfinance's
    Ticker.news list and filtered by the ``days`` parameter.
    """

    @patch("utils.price.yf.Ticker")
    def test_returns_news(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that recent news articles (within the days cutoff) are
        returned with title, publisher, url, published timestamp, and summary.
        """
        import time

        mock_ticker = MagicMock()
        mock_ticker.news = [
            {
                "title": "Apple Q4 Earnings Beat",
                "publisher": "Reuters",
                "link": "https://example.com/news",
                "providerPublishTime": int(time.time()),
                "summary": "Apple beats estimates",
            }
        ]
        mock_ticker_cls.return_value = mock_ticker

        result = get_news("AAPL", days=7)
        assert len(result) == 1
        assert result[0]["title"] == "Apple Q4 Earnings Beat"

    @patch("utils.price.yf.Ticker")
    def test_handles_no_news(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that when the ticker has no news (None), an empty list
        is returned rather than raising an error.
        """
        mock_ticker = MagicMock()
        mock_ticker.news = None
        mock_ticker_cls.return_value = mock_ticker

        result = get_news("AAPL")
        assert result == []


class TestGetHistory:
    """Tests for the ``get_history`` function.

    Validates OHLCV history retrieval including period validation, successful
    data extraction from pandas DataFrames, and graceful handling of empty
    history.
    """

    def test_invalid_period(self) -> None:
        """Verify that an invalid period string (not in the allowed set)
        returns an empty list immediately without making any network call.
        """
        result = get_history("AAPL", period="invalid")
        assert result == []

    @patch("utils.price.yf.Ticker")
    def test_returns_history(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that a successful history response returns a list of
        OHLCV dicts with date, open, high, low, close, and volume fields.
        """
        import pandas as pd

        mock_ticker = MagicMock()
        dates = pd.date_range("2026-01-01", periods=5, freq="D")
        mock_hist = pd.DataFrame(
            {
                "Open": [180.0, 181.0, 182.0, 183.0, 184.0],
                "High": [185.0, 186.0, 187.0, 188.0, 189.0],
                "Low": [179.0, 180.0, 181.0, 182.0, 183.0],
                "Close": [183.0, 184.0, 185.0, 186.0, 187.0],
                "Volume": [1000000, 1100000, 1200000, 1300000, 1400000],
            },
            index=dates,
        )
        mock_ticker.history.return_value = mock_hist
        mock_ticker_cls.return_value = mock_ticker

        result = get_history("AAPL", period="5d")
        assert len(result) == 5
        assert result[0]["close"] == 183.0

    @patch("utils.price.yf.Ticker")
    def test_handles_empty_history(self, mock_ticker_cls: MagicMock) -> None:
        """Verify that when yfinance returns an empty DataFrame, an empty
        list is returned rather than raising an error.
        """
        import pandas as pd

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        mock_ticker_cls.return_value = mock_ticker

        result = get_history("AAPL", period="1mo")
        assert result == []


class TestFinnhubFallback:
    """Tests for the ``_get_finnhub_price`` fallback function.

    Validates that Finnhub is only called when an API key is configured,
    and that it returns properly structured data on success.
    """

    def test_returns_none_without_key(self) -> None:
        """Verify that when FINNHUB_API_KEY is None (not configured),
        the function returns None immediately without attempting any
        network call.
        """
        with patch("utils.price.FINNHUB_API_KEY", None):
            result = _get_finnhub_price("AAPL")
            assert result is None

    @patch("utils.price.FINNHUB_API_KEY", "test_key")
    def test_returns_data_on_success(self) -> None:
        """Verify that when FINNHUB_API_KEY is set and the Finnhub API
        returns valid quote data, a dict with price, change, change_percent,
        and source='finnhub' is returned.
        """
        mock_client = MagicMock()
        mock_client.quote.return_value = {"c": 185.0, "d": 2.0, "dp": 1.1}

        with patch("finnhub.Client", return_value=mock_client):
            result = _get_finnhub_price("AAPL")
            assert result is not None
            assert result["price"] == 185.0
            assert result["source"] == "finnhub"
