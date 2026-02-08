"""Polymarket prediction market data.

This module provides an interface to the Polymarket Gamma API for retrieving
prediction market data. Prediction markets are useful in the money_thoughts
system as a supplementary signal: they aggregate crowd wisdom about future
events (e.g. "Will the Fed raise rates?", "Will AAPL hit $250 by June?")
and can provide context when developing investment theses.

The module interacts with the **Gamma API** (``gamma-api.polymarket.com``),
which is Polymarket's public read-only API for querying events and markets.
It does NOT interact with the CLOB (order book) API and does not place any
trades.

Rate limiting is enforced via a simple sleep-based throttle, configured by
``POLYMARKET_RATE_LIMIT`` in ``utils.config`` (default: 10 requests/second).

Exception hierarchy:
    - ``PolymarketError`` -- base class for all Polymarket-related errors
    - ``MarketNotFoundError(PolymarketError)`` -- raised when a market ID
      does not exist or an endpoint returns 404
    - ``RateLimitError(PolymarketError)`` -- raised when the API returns
      HTTP 429 (rate limit exceeded)

Functions:
    - ``search_markets(query, limit)`` -- keyword search across Polymarket events
    - ``get_market(market_id)`` -- fetch details for a single market by ID
    - ``get_related_markets(symbol)`` -- find markets related to a stock ticker

Internal helpers:
    - ``_rate_limit()`` -- enforce inter-request delay
    - ``_make_request(endpoint, params)`` -- HTTP GET wrapper with error handling
    - ``_format_market(market)`` -- normalise API response into a standard dict
"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from utils.config import POLYMARKET_GAMMA_URL, POLYMARKET_RATE_LIMIT

# Track last request time for rate limiting
_last_request_time = 0.0


class PolymarketError(Exception):
    """Base exception for all Polymarket API errors.

    Raised when a request to the Polymarket Gamma API fails for any reason
    other than rate limiting or a 404 (which have their own subclasses).
    Also serves as the catch-all parent for the exception hierarchy.
    """


class MarketNotFoundError(PolymarketError):
    """Raised when a requested market ID does not exist on Polymarket.

    This maps to an HTTP 404 response from the Gamma API, or to a response
    that returns an empty body when a specific market was requested by ID.
    """


class RateLimitError(PolymarketError):
    """Raised when the Polymarket Gamma API returns HTTP 429 (rate limit exceeded).

    The caller should back off and retry after a delay. In practice, the
    ``_rate_limit()`` helper should prevent this from happening under normal
    usage, but it can occur if multiple processes share the same IP.
    """


def _rate_limit() -> None:
    """Enforce a minimum delay between consecutive Polymarket API requests.

    Uses the module-level ``_last_request_time`` to track when the last
    request was sent. The minimum interval is computed as
    ``1.0 / POLYMARKET_RATE_LIMIT`` seconds (default: 0.1 s for 10 req/s).
    If insufficient time has elapsed, this function sleeps for the remaining
    duration.

    Side effects:
        - Mutates the module-level ``_last_request_time`` global.
        - May call ``time.sleep()`` to enforce the delay.
    """
    global _last_request_time
    min_interval = 1.0 / POLYMARKET_RATE_LIMIT
    elapsed = time.time() - _last_request_time
    if elapsed < min_interval:
        time.sleep(min_interval - elapsed)
    _last_request_time = time.time()


def _make_request(endpoint: str, params: dict | None = None) -> dict | list:
    """Make an HTTP GET request to the Polymarket Gamma API.

    Constructs the full URL by appending ``endpoint`` to the configured
    ``POLYMARKET_GAMMA_URL`` base, sends the request with a 10-second
    timeout, and returns the parsed JSON response.

    Parameters:
        endpoint: API path to append to the base URL (e.g. ``"/events"``
            or ``"/markets/abc123"``).
        params: Optional query parameters to include in the GET request
            (e.g. ``{"title_like": "BTC", "limit": 5}``).

    Returns:
        The parsed JSON response body, which may be a dict (single resource)
        or a list (collection of resources), depending on the endpoint.

    Raises:
        RateLimitError: If the API responds with HTTP 429.
        MarketNotFoundError: If the API responds with HTTP 404.
        PolymarketError: For any other request failure, including network
            errors, timeouts, and non-2xx status codes.

    Side effects:
        - Calls ``_rate_limit()`` which may sleep.
        - Makes one HTTP GET request to the Polymarket Gamma API.
    """
    _rate_limit()

    url = f"{POLYMARKET_GAMMA_URL}{endpoint}"
    try:
        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 429:
            raise RateLimitError("API rate limit exceeded")
        if response.status_code == 404:
            raise MarketNotFoundError(f"Endpoint not found: {endpoint}")

        response.raise_for_status()
        return response.json()

    except requests.RequestException as e:
        raise PolymarketError(f"Request failed: {e}") from e


def _format_market(market: dict) -> dict[str, Any]:
    """Normalise a raw Polymarket API market response into a standard dict.

    The Polymarket API returns market data in slightly different shapes
    depending on whether it comes from the events endpoint or the markets
    endpoint, and whether the market uses binary tokens or outcome prices.
    This function handles both variants and produces a uniform output.

    Probability extraction logic:
        1. If the market has a ``tokens`` list, iterate through it looking
           for an outcome named ``"Yes"`` and use its price as the probability.
           All token outcomes are also collected into the ``outcomes`` list.
        2. If no probability was found from tokens and the market has
           ``outcomePrices`` (a JSON string like ``"[0.65, 0.35]"``), parse
           it and use the first element as the probability.

    Parameters:
        market: Raw dict from the Polymarket Gamma API. May contain various
            combinations of keys depending on the endpoint and market type.

    Returns:
        A normalised dict with the following keys:
            - ``id`` (str | None): Market or condition ID
            - ``question`` (str | None): The prediction question text
            - ``description`` (str | None): Longer description of the market
            - ``probability`` (float | None): Probability of "Yes" outcome (0.0-1.0)
            - ``volume`` (float | None): Total trading volume
            - ``liquidity`` (float | None): Current liquidity in the market
            - ``end_date`` (str | None): ISO date when the market resolves
            - ``created`` (str | None): ISO date when the market was created
            - ``outcomes`` (list[dict] | None): List of outcome dicts with
              ``name`` and ``probability`` keys, or None if no token data
            - ``url`` (str | None): Public Polymarket URL for this event,
              constructed from the ``slug`` field; None if slug is missing
    """
    probability = None
    outcomes: list[dict[str, Any]] = []

    if "tokens" in market and market["tokens"]:
        for token in market["tokens"]:
            outcome_name = token.get("outcome", "Unknown")
            outcome_prob = token.get("price")
            if outcome_prob is not None:
                outcomes.append(
                    {
                        "name": outcome_name,
                        "probability": float(outcome_prob),
                    }
                )
                if outcome_name.lower() == "yes":
                    probability = float(outcome_prob)

    if probability is None and "outcomePrices" in market:
        try:
            prices = market["outcomePrices"]
            if isinstance(prices, str):
                prices = json.loads(prices)
            if prices and len(prices) > 0:
                probability = float(prices[0])
        except (ValueError, IndexError, TypeError):
            pass

    return {
        "id": market.get("id") or market.get("condition_id"),
        "question": market.get("question"),
        "description": market.get("description"),
        "probability": probability,
        "volume": market.get("volume"),
        "liquidity": market.get("liquidity"),
        "end_date": market.get("end_date_iso") or market.get("endDate"),
        "created": market.get("created_at") or market.get("createdAt"),
        "outcomes": outcomes if outcomes else None,
        "url": (
            f"https://polymarket.com/event/{market.get('slug')}" if market.get("slug") else None
        ),
    }


def search_markets(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search for prediction markets on Polymarket related to a query string.

    Queries the Gamma API ``/events`` endpoint with a title-like filter and
    extracts individual markets from the returned event objects. Events on
    Polymarket can contain multiple sub-markets (e.g. an event "US Elections"
    might have markets for each candidate). This function flattens those into
    a single list, respecting the ``limit``.

    Parameters:
        query: Free-text search term (e.g. ``"Apple stock"``, ``"AAPL"``,
            ``"interest rates"``). Matched against event titles.
        limit: Maximum number of market results to return. Defaults to 5.

    Returns:
        A list of normalised market dicts (see ``_format_market`` for the
        schema). Each dict's ``question`` field is guaranteed to be non-None
        (falls back to the event title if the market itself has no question).

        Returns an empty list if:
        - No events match the query
        - The API returns an error (caught and swallowed)

    Side effects:
        - Makes one HTTP GET request to the Polymarket Gamma API.

    Raises:
        Never raises to the caller -- all ``PolymarketError`` exceptions
        are caught and result in an empty list being returned. This is
        intentional: prediction market data is supplementary and should
        never block the main workflow.
    """
    try:
        data = _make_request("/events", params={"title_like": query, "limit": limit})

        if not data:
            return []

        results: list[dict[str, Any]] = []
        for event in data:
            markets = event.get("markets", [])
            if markets:
                for market in markets[: limit - len(results)]:
                    market_data = {**market, **event}
                    formatted = _format_market(market_data)
                    formatted["question"] = formatted["question"] or event.get("title")
                    results.append(formatted)
                    if len(results) >= limit:
                        break
            else:
                formatted = _format_market(event)
                formatted["question"] = formatted["question"] or event.get("title")
                results.append(formatted)

            if len(results) >= limit:
                break

        return results[:limit]

    except (MarketNotFoundError, PolymarketError):
        return []


def get_market(market_id: str) -> dict[str, Any]:
    """Get detailed information for a specific Polymarket market by its ID.

    Fetches a single market from the ``/markets/{market_id}`` endpoint and
    returns it in the normalised format.

    Parameters:
        market_id: The Polymarket market ID string (e.g. a UUID or
            condition ID).

    Returns:
        A normalised market dict (see ``_format_market`` for the schema).

    Raises:
        MarketNotFoundError: If the API returns no data for the given ID,
            indicating the market does not exist.
        PolymarketError: On other API errors (network failures, non-2xx
            responses).
        RateLimitError: If the API returns HTTP 429.

    Side effects:
        - Makes one HTTP GET request to the Polymarket Gamma API.
    """
    data = _make_request(f"/markets/{market_id}")

    if not data:
        raise MarketNotFoundError(f"Market not found: {market_id}")

    return _format_market(data)


def get_related_markets(symbol: str) -> list[dict[str, Any]]:
    """Find Polymarket prediction markets related to a given stock ticker.

    Performs multiple search queries using variations of the ticker symbol
    (plain symbol, "symbol stock", "symbol price") to maximise coverage,
    then deduplicates results by market ID and sorts by trading volume
    (highest first). Returns at most 10 markets.

    This is useful during the ``/research`` and ``/discover`` skills to
    surface prediction-market sentiment alongside traditional financial data.

    Parameters:
        symbol: Stock ticker symbol (e.g. ``"AAPL"``). Used as the base
            for generating search queries.

    Returns:
        A list of normalised market dicts (see ``_format_market``), sorted
        by ``volume`` descending, capped at 10 results. Markets that appear
        in multiple search results are included only once.

        Returns an empty list if all search queries fail or return no results.

    Side effects:
        - Makes up to 3 HTTP GET requests to the Polymarket Gamma API
          (one per search query variant).
    """
    all_results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    queries = [
        symbol,
        f"{symbol} stock",
        f"{symbol} price",
    ]

    for query in queries:
        try:
            results = search_markets(query, limit=10)
            for market in results:
                market_id = market.get("id")
                if market_id and market_id not in seen_ids:
                    seen_ids.add(market_id)
                    all_results.append(market)
        except PolymarketError:
            continue

    all_results.sort(key=lambda x: x.get("volume") or 0, reverse=True)

    return all_results[:10]
