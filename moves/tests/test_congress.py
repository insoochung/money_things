"""Tests for the CongressTradesEngine.

Tests scraping/parsing, trade storage, overlap detection, and signal generation
using mocked HTTP responses.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from engine.congress import CongressTradesEngine
from engine.signals import SignalEngine

SAMPLE_HTML = """
<html><body><table><tbody>
<tr>
    <td>Nancy Pelosi</td>
    <td>NVDA</td>
    <td>Purchase</td>
    <td>$1,000,001 - $5,000,000</td>
    <td>2026-01-15</td>
</tr>
<tr>
    <td>Dan Crenshaw</td>
    <td>MSFT</td>
    <td>Sale (Full)</td>
    <td>$100,001 - $250,000</td>
    <td>2026-01-14</td>
</tr>
<tr>
    <td>Tommy Tuberville</td>
    <td>AVGO</td>
    <td>Purchase</td>
    <td>$50,001 - $100,000</td>
    <td>2026-01-13</td>
</tr>
</tbody></table></body></html>
"""


@pytest.fixture
def congress_engine(seeded_db):
    """CongressTradesEngine with seeded database."""
    return CongressTradesEngine(seeded_db)


@pytest.fixture
def congress_engine_with_signals(seeded_db):
    """CongressTradesEngine with a real SignalEngine."""
    signal_engine = SignalEngine(seeded_db)
    return CongressTradesEngine(seeded_db, signal_engine=signal_engine)


class TestFetchRecent:
    """Tests for fetching and parsing congress trades."""

    @patch("engine.congress.httpx.Client")
    def test_fetch_parses_html(self, mock_client_cls, congress_engine):
        """Trades are parsed from HTML table rows."""
        mock_resp = MagicMock()
        mock_resp.text = SAMPLE_HTML
        mock_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        trades = congress_engine.fetch_recent(days=30)
        assert len(trades) == 3
        assert trades[0]["politician"] == "Nancy Pelosi"
        assert trades[0]["symbol"] == "NVDA"
        assert trades[0]["action"] == "buy"
        assert trades[1]["action"] == "sell"

    def test_fetch_graceful_on_error(self, congress_engine):
        """Returns empty list when HTTP request fails."""
        with patch("engine.congress.httpx.Client", side_effect=Exception("network error")):
            trades = congress_engine.fetch_recent()
        assert trades == []


class TestStoreTrades:
    """Tests for storing trades and deduplication."""

    def test_store_inserts_new(self, congress_engine):
        """New trades are inserted into the database."""
        trades = [
            {
                "politician": "Pelosi",
                "symbol": "NVDA",
                "action": "buy",
                "amount_range": "$1M+",
                "date_filed": "2026-01-15",
                "date_traded": "2026-01-10",
                "source_url": "https://example.com",
            }
        ]
        count = congress_engine.store_trades(trades)
        assert count == 1

        row = congress_engine.db.fetchone("SELECT * FROM congress_trades WHERE symbol = 'NVDA'")
        assert row is not None
        assert row["politician"] == "Pelosi"

    def test_store_skips_duplicates(self, congress_engine):
        """Duplicate trades (same member+symbol+date) are skipped."""
        trade = {
            "politician": "Pelosi",
            "symbol": "NVDA",
            "action": "buy",
            "amount_range": "$1M+",
            "date_filed": "2026-01-15",
            "date_traded": "2026-01-10",
            "source_url": "https://example.com",
        }
        assert congress_engine.store_trades([trade]) == 1
        assert congress_engine.store_trades([trade]) == 0


class TestCheckOverlap:
    """Tests for cross-referencing trades with portfolio."""

    def test_overlap_with_thesis_symbols(self, congress_engine):
        """Trades matching thesis symbols are detected as overlapping."""
        # Thesis has NVDA and AVGO (from seeded_db)
        congress_engine.store_trades(
            [
                {
                    "politician": "Pelosi",
                    "symbol": "NVDA",
                    "action": "buy",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
                {
                    "politician": "Someone",
                    "symbol": "XYZ",
                    "action": "buy",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
            ]
        )
        overlapping = congress_engine.check_overlap()
        symbols = [t["symbol"] for t in overlapping]
        assert "NVDA" in symbols
        assert "XYZ" not in symbols

    def test_no_overlap_returns_empty(self, seeded_db):
        """No overlap returns empty list when no trades match."""
        engine = CongressTradesEngine(seeded_db)
        engine.store_trades(
            [
                {
                    "politician": "Someone",
                    "symbol": "ZZZZZ",
                    "action": "buy",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
            ]
        )
        assert engine.check_overlap() == []


class TestGenerateSignals:
    """Tests for signal generation from overlapping trades."""

    def test_generates_buy_signals(self, congress_engine_with_signals):
        """BUY signals are generated for overlapping congress buys."""
        engine = congress_engine_with_signals
        engine.store_trades(
            [
                {
                    "politician": "Pelosi",
                    "symbol": "NVDA",
                    "action": "buy",
                    "amount_range": "$1M+",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
            ]
        )
        signals = engine.generate_signals()
        assert len(signals) == 1
        assert signals[0].symbol == "NVDA"
        assert signals[0].action.value == "BUY"
        assert signals[0].confidence == 0.3
        assert signals[0].source.value == "congress_trade"

    def test_no_signals_for_sells(self, congress_engine_with_signals):
        """No signals are generated for congress sells."""
        engine = congress_engine_with_signals
        engine.store_trades(
            [
                {
                    "politician": "Someone",
                    "symbol": "NVDA",
                    "action": "sell",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
            ]
        )
        signals = engine.generate_signals()
        assert len(signals) == 0

    def test_no_signals_without_engine(self, congress_engine):
        """Returns empty list when no signal_engine is configured."""
        assert congress_engine.generate_signals() == []


class TestGetSummary:
    """Tests for the summary method."""

    def test_summary_counts(self, congress_engine):
        """Summary returns correct counts."""
        engine = congress_engine
        engine.store_trades(
            [
                {
                    "politician": "A",
                    "symbol": "NVDA",
                    "action": "buy",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-10",
                    "source_url": "",
                },
                {
                    "politician": "B",
                    "symbol": "NVDA",
                    "action": "buy",
                    "amount_range": "",
                    "date_filed": "2026-01-15",
                    "date_traded": "2026-01-11",
                    "source_url": "",
                },
            ]
        )
        engine.check_overlap()  # Mark overlaps
        summary = engine.get_summary()
        assert summary["total_trades"] == 2
        assert summary["overlapping"] == 2
        assert summary["net_by_symbol"]["NVDA"] == 2
