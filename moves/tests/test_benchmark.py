"""Tests for api.benchmark module — period returns, alignment, stats."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from api.benchmark import (
    align_series,
    calculate_period_return,
    compute_benchmark_stats,
    daily_returns,
    period_start_date,
)

# ---------------------------------------------------------------------------
# period_start_date
# ---------------------------------------------------------------------------

class TestPeriodStartDate:
    """Tests for period_start_date helper."""

    def test_ytd(self) -> None:
        d = date(2026, 3, 15)
        assert period_start_date("ytd", d) == "2026-01-01"

    def test_mtd(self) -> None:
        d = date(2026, 3, 15)
        assert period_start_date("mtd", d) == "2026-03-01"

    def test_wtd_monday(self) -> None:
        # 2026-02-09 is a Monday
        d = date(2026, 2, 9)
        assert period_start_date("wtd", d) == "2026-02-09"

    def test_wtd_wednesday(self) -> None:
        d = date(2026, 2, 11)  # Wednesday
        assert period_start_date("wtd", d) == "2026-02-09"


# ---------------------------------------------------------------------------
# daily_returns
# ---------------------------------------------------------------------------

class TestDailyReturns:
    """Tests for daily_returns calculation."""

    def test_simple(self) -> None:
        vals = [100.0, 110.0, 99.0]
        rets = daily_returns(vals)
        np.testing.assert_allclose(rets, [10.0, -10.0], atol=0.01)

    def test_single_value_returns_empty(self) -> None:
        assert len(daily_returns([100.0])) == 0


# ---------------------------------------------------------------------------
# align_series
# ---------------------------------------------------------------------------

class TestAlignSeries:
    """Tests for portfolio/benchmark date alignment."""

    def test_common_dates_only(self) -> None:
        pf = [
            {"date": "2026-01-01", "total_value": 100},
            {"date": "2026-01-02", "total_value": 105},
            {"date": "2026-01-03", "total_value": 110},
        ]
        bm = [
            {"date": "2026-01-02", "close": 400},
            {"date": "2026-01-03", "close": 410},
        ]
        pf_vals, bm_vals, dates = align_series(pf, bm)
        assert dates == ["2026-01-02", "2026-01-03"]
        assert pf_vals == [105, 110]
        assert bm_vals == [400, 410]


# ---------------------------------------------------------------------------
# compute_benchmark_stats
# ---------------------------------------------------------------------------

class TestComputeBenchmarkStats:
    """Tests for benchmark statistics computation."""

    def test_identical_series(self) -> None:
        """Identical series: beta=1, alpha=0, corr=1."""
        rets = np.array([1.0, -0.5, 0.8, -0.3, 0.6] * 10)
        stats = compute_benchmark_stats(rets, rets)
        assert abs(stats["beta"] - 1.0) < 0.01
        assert abs(stats["correlation"] - 1.0) < 0.01
        assert abs(stats["alpha_pct"]) < 0.5

    def test_insufficient_data(self) -> None:
        stats = compute_benchmark_stats(np.array([1.0]), np.array([1.0]))
        assert stats["beta"] == 0.0

    def test_up_down_capture(self) -> None:
        """Portfolio that doubles benchmark returns."""
        bm = np.array([1.0, -1.0, 2.0, -2.0, 1.5])
        pf = bm * 2
        stats = compute_benchmark_stats(pf, bm)
        assert stats["up_capture_pct"] == pytest.approx(200.0, abs=1)
        assert stats["down_capture_pct"] == pytest.approx(200.0, abs=1)


# ---------------------------------------------------------------------------
# calculate_period_return
# ---------------------------------------------------------------------------

class TestCalculatePeriodReturn:
    """Tests for period return calculation."""

    def test_basic_return(self) -> None:
        data = [
            {"date": "2026-01-01", "total_value": 100},
            {"date": "2026-01-15", "total_value": 110},
            {"date": "2026-02-01", "total_value": 120},
        ]
        ret = calculate_period_return(data, "2026-01-01")
        assert ret == pytest.approx(20.0)

    def test_mid_period(self) -> None:
        data = [
            {"date": "2026-01-01", "total_value": 100},
            {"date": "2026-01-15", "total_value": 110},
            {"date": "2026-02-01", "total_value": 120},
        ]
        ret = calculate_period_return(data, "2026-01-10")
        # Closest >= 2026-01-10 is 2026-01-15 (110), end=120
        assert ret == pytest.approx(9.0909, abs=0.01)

    def test_empty_data(self) -> None:
        assert calculate_period_return([], "2026-01-01") == 0.0
