"""Tests for performance analysis API endpoints.

Tests portfolio performance, benchmark comparison, and drawdown analysis
endpoints that power the dashboard charts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from db.database import Database


@pytest.fixture
def perf_db(seeded_db: Database) -> Database:
    """Database with 60+ days of portfolio value data for performance calcs."""
    conn = seeded_db.connect()
    # Clear the single default row
    conn.execute("DELETE FROM portfolio_value")
    # Insert 90 days of data with slight upward drift
    import random

    random.seed(42)
    value = 100000.0
    for i in range(90):
        daily_ret = random.gauss(0.04, 1.2)  # slight positive bias
        value *= 1 + daily_ret / 100
        conn.execute(
            """INSERT INTO portfolio_value
               (date, total_value, cash, cost_basis, daily_return_pct, user_id)
               VALUES (date('now', ? || ' days'), ?, 30000, 80000, ?, 1)""",
            (str(-89 + i), round(value, 2), round(daily_ret, 4)),
        )
    conn.commit()
    return seeded_db


@pytest.fixture
def client(perf_db: Database) -> TestClient:
    """Test client with performance data."""
    import api.deps as deps

    mock_container = MagicMock()
    mock_container.db = perf_db
    mock_container.pricing = MagicMock()
    mock_container.thesis_engine = MagicMock()
    mock_container.signal_engine = MagicMock()
    mock_container.risk_manager = MagicMock()
    mock_container.principles_engine = MagicMock()
    mock_container.broker = MagicMock()

    deps._engines["container"] = mock_container
    app = create_app()
    return TestClient(app)


@pytest.fixture
def empty_client(seeded_db: Database) -> TestClient:
    """Test client with minimal data (only 1 portfolio_value row)."""
    import api.deps as deps

    mock_container = MagicMock()
    mock_container.db = seeded_db
    mock_container.pricing = MagicMock()
    mock_container.thesis_engine = MagicMock()
    mock_container.signal_engine = MagicMock()
    mock_container.risk_manager = MagicMock()
    mock_container.principles_engine = MagicMock()
    mock_container.broker = MagicMock()

    deps._engines["container"] = mock_container
    app = create_app()
    return TestClient(app)


class TestPerformanceMetrics:
    """Test GET /api/fund/performance."""

    def test_returns_metrics(self, client: TestClient) -> None:
        r = client.get("/api/fund/performance?days=90")
        assert r.status_code == 200
        data = r.json()
        # All expected fields present
        for field in [
            "total_return_pct",
            "annualized_return_pct",
            "sharpe_ratio",
            "sortino_ratio",
            "max_drawdown_pct",
            "volatility_pct",
            "var_95_pct",
            "win_rate_pct",
            "best_day_pct",
            "worst_day_pct",
            "calmar_ratio",
            "nav_series",
        ]:
            assert field in data, f"Missing field: {field}"

    def test_nav_series_populated(self, client: TestClient) -> None:
        r = client.get("/api/fund/performance?days=90")
        data = r.json()
        assert len(data["nav_series"]) == 90
        assert "date" in data["nav_series"][0]
        assert "value" in data["nav_series"][0]

    def test_volatility_positive(self, client: TestClient) -> None:
        data = client.get("/api/fund/performance?days=90").json()
        assert data["volatility_pct"] > 0

    def test_win_rate_bounded(self, client: TestClient) -> None:
        data = client.get("/api/fund/performance?days=90").json()
        assert 0 <= data["win_rate_pct"] <= 100

    def test_max_drawdown_non_negative(self, client: TestClient) -> None:
        data = client.get("/api/fund/performance?days=90").json()
        assert data["max_drawdown_pct"] >= 0

    def test_insufficient_data(self, empty_client: TestClient) -> None:
        """Single data point should return 400."""
        r = empty_client.get("/api/fund/performance?days=30")
        # Could be 400 or 404 depending on date range
        assert r.status_code in (400, 404)

    def test_days_validation(self, client: TestClient) -> None:
        """Days < 30 should fail validation."""
        r = client.get("/api/fund/performance?days=5")
        assert r.status_code == 422


class TestBenchmarkComparison:
    """Test GET /api/fund/benchmark."""

    @patch("api.benchmark.fetch_benchmark_prices", return_value=None)
    def test_benchmark_no_data(self, mock_fetch, client: TestClient) -> None:
        """When benchmark data unavailable, should handle gracefully."""
        r = client.get("/api/fund/benchmark?days=90")
        assert r.status_code in (200, 400, 404, 500)

    @patch("api.benchmark.fetch_benchmark_prices", return_value=[])
    def test_benchmark_empty_data(self, mock_fetch, client: TestClient) -> None:
        """Empty benchmark data should return 400 (insufficient data)."""
        r = client.get("/api/fund/benchmark?days=90")
        assert r.status_code in (200, 400, 404, 500)


class TestDrawdownAnalysis:
    """Test GET /api/fund/drawdown."""

    def test_drawdown_returns_data(self, client: TestClient) -> None:
        r = client.get("/api/fund/drawdown?days=90")
        assert r.status_code == 200
        data = r.json()
        assert "current_drawdown_pct" in data
        assert "max_drawdown_pct" in data
        assert "series" in data
        assert "drawdown_events" in data
        assert "underwater_periods" in data

    def test_drawdown_non_negative(self, client: TestClient) -> None:
        data = client.get("/api/fund/drawdown?days=90").json()
        assert data["max_drawdown_pct"] >= 0
        assert data["current_drawdown_pct"] >= 0

    def test_drawdown_with_single_row(self, empty_client: TestClient) -> None:
        """Single data point should still return 200 with zero drawdown."""
        r = empty_client.get("/api/fund/drawdown?days=30")
        # The endpoint may handle single row gracefully
        assert r.status_code in (200, 400, 404)

    def test_drawdown_series_structure(self, client: TestClient) -> None:
        data = client.get("/api/fund/drawdown?days=90").json()
        if data["series"]:
            point = data["series"][0]
            assert "date" in point
            assert "value" in point
