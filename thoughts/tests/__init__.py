"""Test suite for the money_thoughts utilities package.

This package contains unit tests for all utility modules in the money_thoughts
system. Tests are designed to run with pytest and use mocking extensively to
avoid making real network calls or depending on external services.

Test modules:
    - ``test_price`` -- Tests for ``utils.price`` (yfinance/Finnhub market data)
    - ``test_charts`` -- Tests for ``utils.charts`` (Obsidian Charts generation)
    - ``test_polymarket`` -- Tests for ``utils.polymarket`` (prediction market data)

Testing conventions:
    - All external API calls (yfinance, Finnhub, Polymarket) are mocked via
      ``unittest.mock.patch`` to ensure tests are fast, deterministic, and
      do not require API keys or network access.
    - Database-dependent tests (charts) use a temporary SQLite database via
      pytest's ``tmp_path`` fixture.
    - Test classes are named ``Test<FunctionName>`` and group related test
      cases for a single function or feature.

Running tests::

    cd thoughts/
    source .venv/bin/activate
    python -m pytest tests/ -q
"""
