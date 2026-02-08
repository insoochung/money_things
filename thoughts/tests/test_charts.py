"""Tests for utils/charts.py -- chart generation.

This module contains unit tests for the ``utils.charts`` module, which generates
Obsidian Charts-compatible YAML code blocks from SQLite price and portfolio data.

All tests use a temporary SQLite database (via pytest's ``tmp_path`` fixture)
to avoid interfering with the real ``data/journal.db`` file. The ``_temp_db``
autouse fixture patches ``db.DB_PATH`` for every test, ensuring complete
isolation.

Test classes:
    - ``TestToChartYaml`` -- Tests for the ``_to_chart_yaml`` YAML formatter
    - ``TestSamplePoints`` -- Tests for the ``_sample_points`` down-sampler
    - ``TestPriceChart`` -- Tests for ``price_chart()`` (single-symbol chart)
    - ``TestMultiPriceChart`` -- Tests for ``multi_price_chart()`` (multi-symbol)
    - ``TestPortfolioValueChart`` -- Tests for ``portfolio_value_chart()``

Data setup pattern:
    Tests that need database data (price charts, portfolio charts) insert
    rows directly via ``db.store_price`` or ``db.record_portfolio_value``
    within the test method, after the ``_temp_db`` fixture has already
    initialised the temporary database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from utils import db
from utils.charts import (
    _sample_points,
    _to_chart_yaml,
    multi_price_chart,
    portfolio_value_chart,
    price_chart,
)


@pytest.fixture(autouse=True)
def _temp_db(tmp_path: Path) -> None:
    """Use a temporary SQLite database for each test.

    Patches ``db.DB_PATH`` to point to a fresh temporary file, then calls
    ``db.init_db()`` to create the schema. This ensures every test starts
    with a clean, empty database and no test can accidentally read or write
    the real ``data/journal.db``.

    This fixture is ``autouse=True`` so it applies to every test in this
    module without needing to be explicitly requested.

    Yields:
        None. The fixture's purpose is its side effect (patching DB_PATH).
    """
    test_db = tmp_path / "test_journal.db"
    with patch.object(db, "DB_PATH", test_db):
        db.init_db()
        yield


class TestToChartYaml:
    """Tests for the ``_to_chart_yaml`` internal helper.

    Validates that chart dicts are correctly converted to the inline-array
    YAML format required by the Obsidian Charts plugin.
    """

    def test_basic_chart(self) -> None:
        """Verify that a single-series chart dict produces valid YAML with
        inline arrays for labels and data, plus the correct type and title.
        """
        chart = {
            "type": "line",
            "labels": ["2026-01-01", "2026-01-02"],
            "series": [{"title": "AAPL", "data": [183.0, 185.0]}],
        }
        result = _to_chart_yaml(chart)
        assert "type: line" in result
        assert "labels: [2026-01-01, 2026-01-02]" in result
        assert "title: AAPL" in result
        assert "data: [183.0, 185.0]" in result

    def test_multi_series(self) -> None:
        """Verify that a multi-series chart dict produces YAML with each
        series listed separately under the 'series:' key.
        """
        chart = {
            "type": "line",
            "labels": ["2026-01-01"],
            "series": [
                {"title": "AAPL", "data": [183.0]},
                {"title": "MSFT", "data": [400.0]},
            ],
        }
        result = _to_chart_yaml(chart)
        assert "title: AAPL" in result
        assert "title: MSFT" in result


class TestSamplePoints:
    """Tests for the ``_sample_points`` internal helper.

    Validates the down-sampling logic that keeps charts readable by reducing
    large datasets to approximately weekly granularity while preserving all
    points in smaller datasets.
    """

    def test_small_dataset_kept_intact(self) -> None:
        """Verify that datasets with 90 or fewer points are returned
        unchanged (no sampling occurs).
        """
        data = [{"date": f"2026-01-{i:02d}", "value": float(i)} for i in range(1, 31)]
        labels, values = _sample_points(data, "date", "value")
        assert len(labels) == 30

    def test_large_dataset_sampled(self) -> None:
        """Verify that datasets larger than 90 points are down-sampled
        to fewer points, and that the last data point is always included
        to ensure the chart reaches the most recent date.
        """
        data = [
            {"date": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "value": float(i)}
            for i in range(200)
        ]
        labels, values = _sample_points(data, "date", "value")
        assert len(labels) < 200
        # Should include the last point
        assert values[-1] == 199.0


class TestPriceChart:
    """Tests for the ``price_chart`` function.

    Validates end-to-end chart generation from database price data, including
    the empty-data edge case.
    """

    def test_returns_empty_for_no_data(self) -> None:
        """Verify that when no price data exists for a symbol, an empty
        string is returned (not a chart block with empty data).
        """
        result = price_chart("ZZZZ", period_days=30)
        assert result == ""

    def test_generates_chart_block(self) -> None:
        """Verify that when price data exists in the database, a properly
        formatted ```chart code block is generated containing the ticker
        symbol and YAML chart specification.
        """
        end = date.today()
        for i in range(10):
            d = end - timedelta(days=10 - i)
            db.store_price("AAPL", close=180.0 + i, timestamp=datetime(d.year, d.month, d.day))

        result = price_chart("AAPL", period_days=30)
        assert result.startswith("```chart")
        assert "AAPL" in result
        assert result.endswith("```")


class TestMultiPriceChart:
    """Tests for the ``multi_price_chart`` function.

    Validates multi-symbol chart generation including the normalized
    (percentage change) mode and the empty-data edge case.
    """

    def test_returns_empty_for_no_data(self) -> None:
        """Verify that when no price data exists for any of the given
        symbols, an empty string is returned.
        """
        result = multi_price_chart(["ZZZZ", "YYYY"])
        assert result == ""

    def test_normalized_chart(self) -> None:
        """Verify that when normalized=True is passed, a chart is generated
        with both symbols present. In normalized mode, values are converted
        to percentage change from the first data point.
        """
        end = date.today()
        for i in range(5):
            d = end - timedelta(days=5 - i)
            ts = datetime(d.year, d.month, d.day)
            db.store_price("AAPL", close=180.0 + i, timestamp=ts)
            db.store_price("MSFT", close=400.0 + i * 2, timestamp=ts)

        result = multi_price_chart(["AAPL", "MSFT"], period_days=30, normalized=True)
        assert "AAPL" in result
        assert "MSFT" in result


class TestPortfolioValueChart:
    """Tests for the ``portfolio_value_chart`` function.

    Validates portfolio value chart generation from the portfolio_value
    table, including the empty-data edge case.
    """

    def test_returns_empty_for_no_data(self) -> None:
        """Verify that when no portfolio snapshots exist, an empty string
        is returned.
        """
        result = portfolio_value_chart()
        assert result == ""

    def test_generates_chart(self) -> None:
        """Verify that when portfolio value snapshots exist in the database,
        a chart block is generated with 'Portfolio Value' as the series title.
        """
        end = date.today()
        for i in range(5):
            d = end - timedelta(days=5 - i)
            db.record_portfolio_value(d, total_value=100000.0 + i * 100)

        result = portfolio_value_chart(period_days=30)
        assert "Portfolio Value" in result
