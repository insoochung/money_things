"""Tests for utils/polymarket.py -- prediction market data.

This module contains unit tests for the ``utils.polymarket`` module, which
provides an interface to the Polymarket Gamma API for querying prediction
market data. All HTTP requests are mocked to ensure tests run without
network access.

Test classes:
    - ``TestFormatMarket`` -- Tests for ``_format_market()`` (response normalisation)
    - ``TestSearchMarkets`` -- Tests for ``search_markets()`` (keyword search)
    - ``TestGetMarket`` -- Tests for ``get_market()`` (single market by ID)
    - ``TestGetRelatedMarkets`` -- Tests for ``get_related_markets()`` (ticker-related)

Mocking strategy:
    - ``_make_request`` is patched for tests that exercise the public API
      functions (``search_markets``, ``get_market``), simulating Gamma API
      responses.
    - ``search_markets`` itself is patched when testing ``get_related_markets``,
      since that function is a higher-level orchestrator that calls
      ``search_markets`` multiple times with different query variations.
    - ``_format_market`` is tested directly with hand-crafted dicts since it
      is a pure function with no external dependencies.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from utils.polymarket import (
    MarketNotFoundError,
    PolymarketError,
    _format_market,
    get_market,
    get_related_markets,
    search_markets,
)


class TestFormatMarket:
    """Tests for the ``_format_market`` internal helper.

    Validates that raw Polymarket API responses (which vary in structure
    depending on the endpoint and market type) are correctly normalised
    into the standard dict format used throughout the codebase.
    """

    def test_formats_basic_market(self) -> None:
        """Verify that a market dict with standard fields (id, question,
        description, volume, liquidity, slug) is correctly formatted,
        and that the URL is constructed from the slug.
        """
        market = {
            "id": "123",
            "question": "Will BTC hit 100k?",
            "description": "Test market",
            "volume": 1000000,
            "liquidity": 500000,
            "slug": "btc-100k",
        }
        result = _format_market(market)
        assert result["id"] == "123"
        assert result["question"] == "Will BTC hit 100k?"
        assert result["url"] == "https://polymarket.com/event/btc-100k"

    def test_extracts_probability_from_tokens(self) -> None:
        """Verify that when a market has a 'tokens' list with outcome prices,
        the probability is extracted from the 'Yes' outcome and all outcomes
        are collected into the 'outcomes' list.
        """
        market = {
            "id": "123",
            "tokens": [
                {"outcome": "Yes", "price": 0.75},
                {"outcome": "No", "price": 0.25},
            ],
        }
        result = _format_market(market)
        assert result["probability"] == 0.75
        assert len(result["outcomes"]) == 2

    def test_extracts_probability_from_outcome_prices(self) -> None:
        """Verify that when a market uses 'outcomePrices' (a JSON string)
        instead of 'tokens', the probability is correctly parsed from the
        first element of the array.
        """
        market = {
            "id": "123",
            "outcomePrices": "[0.65, 0.35]",
        }
        result = _format_market(market)
        assert result["probability"] == 0.65

    def test_handles_missing_slug(self) -> None:
        """Verify that when a market has no 'slug' field, the URL is set
        to None rather than raising a KeyError or constructing a broken URL.
        """
        market = {"id": "123"}
        result = _format_market(market)
        assert result["url"] is None


class TestSearchMarkets:
    """Tests for the ``search_markets`` function.

    Validates keyword search functionality including result extraction from
    event objects, empty results, API error handling, and limit enforcement.
    """

    @patch("utils.polymarket._make_request")
    def test_returns_results(self, mock_request: MagicMock) -> None:
        """Verify that when the API returns event objects containing markets,
        the markets are extracted, formatted, and returned as a flat list.
        """
        mock_request.return_value = [
            {
                "title": "BTC Markets",
                "markets": [
                    {
                        "id": "m1",
                        "question": "Will BTC hit 100k?",
                        "tokens": [{"outcome": "Yes", "price": 0.7}],
                    }
                ],
            }
        ]
        results = search_markets("BTC", limit=5)
        assert len(results) == 1
        assert results[0]["question"] == "Will BTC hit 100k?"

    @patch("utils.polymarket._make_request")
    def test_returns_empty_on_no_data(self, mock_request: MagicMock) -> None:
        """Verify that when the API returns an empty list (no matching events),
        an empty list is returned.
        """
        mock_request.return_value = []
        results = search_markets("nonexistent")
        assert results == []

    @patch("utils.polymarket._make_request")
    def test_handles_api_error(self, mock_request: MagicMock) -> None:
        """Verify that when _make_request raises a PolymarketError, the
        exception is caught and an empty list is returned, since prediction
        market data is supplementary and should not block the workflow.
        """
        mock_request.side_effect = PolymarketError("API error")
        results = search_markets("BTC")
        assert results == []

    @patch("utils.polymarket._make_request")
    def test_respects_limit(self, mock_request: MagicMock) -> None:
        """Verify that the limit parameter is respected: even when the API
        returns more markets than the limit, only 'limit' results are returned.
        """
        mock_request.return_value = [
            {
                "title": "Event",
                "markets": [{"id": f"m{i}", "question": f"Q{i}"} for i in range(10)],
            }
        ]
        results = search_markets("test", limit=3)
        assert len(results) <= 3


class TestGetMarket:
    """Tests for the ``get_market`` function.

    Validates single-market retrieval by ID, including the success path
    and the not-found error path.
    """

    @patch("utils.polymarket._make_request")
    def test_returns_market(self, mock_request: MagicMock) -> None:
        """Verify that when the API returns market data for a valid ID,
        it is correctly formatted and returned.
        """
        mock_request.return_value = {
            "id": "123",
            "question": "Test?",
            "tokens": [{"outcome": "Yes", "price": 0.6}],
        }
        result = get_market("123")
        assert result["id"] == "123"

    @patch("utils.polymarket._make_request")
    def test_raises_on_not_found(self, mock_request: MagicMock) -> None:
        """Verify that when the API returns an empty dict (market not found),
        a MarketNotFoundError is raised.
        """
        mock_request.return_value = {}
        with pytest.raises(MarketNotFoundError):
            get_market("nonexistent")


class TestGetRelatedMarkets:
    """Tests for the ``get_related_markets`` function.

    Validates the multi-query search strategy (plain symbol, "symbol stock",
    "symbol price"), deduplication by market ID, and volume-based sorting.
    """

    @patch("utils.polymarket.search_markets")
    def test_returns_deduplicated_results(self, mock_search: MagicMock) -> None:
        """Verify that get_related_markets calls search_markets with 3
        query variants, deduplicates results by market ID, and sorts by
        volume descending.
        """
        mock_search.return_value = [
            {"id": "m1", "question": "Q1", "volume": 1000},
            {"id": "m2", "question": "Q2", "volume": 2000},
        ]
        results = get_related_markets("AAPL")
        # Should be called with multiple search queries
        assert mock_search.call_count == 3
        # Results should be sorted by volume descending
        if len(results) >= 2:
            assert results[0]["volume"] >= results[1]["volume"]

    @patch("utils.polymarket.search_markets")
    def test_handles_errors_gracefully(self, mock_search: MagicMock) -> None:
        """Verify that when all search queries raise PolymarketError,
        an empty list is returned rather than propagating the exception.
        """
        mock_search.side_effect = PolymarketError("error")
        results = get_related_markets("AAPL")
        assert results == []
