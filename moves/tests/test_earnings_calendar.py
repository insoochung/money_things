"""Tests for the earnings calendar module."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from engine.earnings_calendar import (
    clear_cache,
    fetch_earnings_date,
    get_next_earnings,
    is_earnings_imminent,
    load_earnings_dates,
)


@pytest.fixture(autouse=True)
def _clear():
    clear_cache()
    yield
    clear_cache()


class TestLoadEarningsDates:
    def test_load_valid(self, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({"META": ["2026-02-15"], "NVDA": ["2026-03-01"]}))
        result = load_earnings_dates(f)
        assert result["META"] == ["2026-02-15"]

    def test_missing_file(self, tmp_path):
        assert load_earnings_dates(tmp_path / "nope.json") == {}

    def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        assert load_earnings_dates(f) == {}


class TestFetchEarningsDate:
    @patch("yfinance.Ticker")
    def test_fetches_from_yfinance_dict(self, mock_ticker_cls):
        """Calendar returned as dict with Earnings Date."""
        import pandas as pd

        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker
        earnings_ts = pd.Timestamp("2026-03-15")
        mock_ticker.calendar = {"Earnings Date": [earnings_ts]}

        result = fetch_earnings_date("META")
        assert result == date(2026, 3, 15)

    @patch("yfinance.Ticker")
    def test_caches_result(self, mock_ticker_cls):
        mock_ticker = MagicMock()
        mock_ticker_cls.return_value = mock_ticker
        mock_ticker.calendar = None

        fetch_earnings_date("XYZ")
        fetch_earnings_date("XYZ")
        # Should only create Ticker once due to cache
        assert mock_ticker_cls.call_count == 1

    @patch("yfinance.Ticker")
    def test_returns_none_on_error(self, mock_ticker_cls):
        mock_ticker_cls.side_effect = Exception("API error")
        assert fetch_earnings_date("BAD") is None


class TestGetNextEarnings:
    def test_static_file_priority(self, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({"META": ["2026-12-01"]}))
        ref = datetime(2026, 1, 1)
        result = get_next_earnings("META", config_path=f, reference_date=ref, use_api=False)
        assert result == date(2026, 12, 1)

    def test_skips_past_dates(self, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({"META": ["2020-01-01", "2026-12-01"]}))
        ref = datetime(2026, 6, 1)
        result = get_next_earnings("META", config_path=f, reference_date=ref, use_api=False)
        assert result == date(2026, 12, 1)

    @patch("engine.earnings_calendar.fetch_earnings_date")
    def test_falls_back_to_api(self, mock_fetch, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({}))
        mock_fetch.return_value = date(2026, 4, 15)

        result = get_next_earnings("NVDA", config_path=f, reference_date=datetime(2026, 3, 1))
        assert result == date(2026, 4, 15)
        mock_fetch.assert_called_once_with("NVDA")

    def test_no_api_returns_none(self, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({}))
        result = get_next_earnings("XYZ", config_path=f, use_api=False)
        assert result is None


class TestIsEarningsImminent:
    def test_within_window(self, tmp_path):
        f = tmp_path / "cal.json"
        target = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        f.write_text(json.dumps({"META": [target]}))
        assert is_earnings_imminent("META", config_path=f, use_api=False)

    def test_outside_window(self, tmp_path):
        f = tmp_path / "cal.json"
        target = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        f.write_text(json.dumps({"META": [target]}))
        assert not is_earnings_imminent("META", config_path=f, use_api=False)

    @patch("engine.earnings_calendar.fetch_earnings_date")
    def test_api_fallback_blocks(self, mock_fetch, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({}))
        mock_fetch.return_value = (datetime.now() + timedelta(days=2)).date()

        assert is_earnings_imminent("NVDA", config_path=f)

    def test_no_data_returns_false(self, tmp_path):
        f = tmp_path / "cal.json"
        f.write_text(json.dumps({}))
        assert not is_earnings_imminent("UNKNOWN", config_path=f, use_api=False)
