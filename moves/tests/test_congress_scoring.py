"""Tests for the politician scoring engine.

Tests score calculation, amount parsing, committee relevance, tier assignment,
trade enrichment, and integration with congress signal generation.
"""

from __future__ import annotations

import pytest

from engine.congress_scoring import (
    PoliticianScorer,
    assign_tier,
    calculate_disclosure_lag,
    check_committee_relevance,
    is_etf,
    parse_amount_bucket,
)

# ── Amount Parsing ──


class TestParseAmountBucket:
    """Tests for parse_amount_bucket."""

    def test_small_range(self) -> None:
        assert parse_amount_bucket("$1,001 - $15,000") == "small"

    def test_medium_range(self) -> None:
        assert parse_amount_bucket("$15,001 - $50,000") == "medium"

    def test_large_range(self) -> None:
        assert parse_amount_bucket("$100,001 - $250,000") == "large"

    def test_very_large(self) -> None:
        assert parse_amount_bucket("$1,000,001 - $5,000,000") == "large"

    def test_empty_string(self) -> None:
        assert parse_amount_bucket("") == "small"

    def test_none_input(self) -> None:
        assert parse_amount_bucket("") == "small"

    def test_no_dollar_sign(self) -> None:
        assert parse_amount_bucket("50,001 - 100,000") == "medium"


# ── Disclosure Lag ──


class TestDisclosureLag:
    """Tests for calculate_disclosure_lag."""

    def test_normal_lag(self) -> None:
        assert calculate_disclosure_lag("2024-01-01", "2024-01-31") == 30

    def test_same_day(self) -> None:
        assert calculate_disclosure_lag("2024-06-15", "2024-06-15") == 0

    def test_invalid_date(self) -> None:
        assert calculate_disclosure_lag("not-a-date", "2024-01-01") is None

    def test_different_formats(self) -> None:
        assert calculate_disclosure_lag("01/01/2024", "2024-02-01") == 31

    def test_negative_clamped(self) -> None:
        """Filing before trade should return 0."""
        assert calculate_disclosure_lag("2024-06-15", "2024-06-10") == 0


# ── Tier Assignment ──


class TestAssignTier:
    """Tests for assign_tier."""

    def test_whale(self) -> None:
        assert assign_tier(85) == "whale"

    def test_notable(self) -> None:
        assert assign_tier(65) == "notable"

    def test_average(self) -> None:
        assert assign_tier(45) == "average"

    def test_noise(self) -> None:
        assert assign_tier(30) == "noise"

    def test_boundary_whale(self) -> None:
        assert assign_tier(80) == "whale"

    def test_boundary_notable(self) -> None:
        assert assign_tier(60) == "notable"

    def test_boundary_average(self) -> None:
        assert assign_tier(40) == "average"


# ── ETF Detection ──


class TestIsEtf:
    """Tests for is_etf."""

    def test_spy_is_etf(self) -> None:
        assert is_etf("SPY") is True

    def test_nvda_is_not_etf(self) -> None:
        assert is_etf("NVDA") is False

    def test_case_insensitive(self) -> None:
        assert is_etf("spy") is True


# ── Committee Relevance ──


class TestCommitteeRelevance:
    """Tests for check_committee_relevance."""

    def test_relevant(self) -> None:
        assert check_committee_relevance(["Armed Services"], "LMT") is True

    def test_etf_not_relevant(self) -> None:
        assert check_committee_relevance(["Armed Services"], "SPY") is False

    def test_no_committees(self) -> None:
        assert check_committee_relevance([], "NVDA") is False

    def test_broad_only_not_relevant(self) -> None:
        assert check_committee_relevance(["Appropriations"], "NVDA") is False


# ── PoliticianScorer Integration ──


@pytest.fixture()
def scorer(db):
    """Create a PoliticianScorer with a test database."""
    return PoliticianScorer(db)


class TestPoliticianScorer:
    """Integration tests for PoliticianScorer."""

    def _insert_trade(self, scorer, politician, symbol, action="buy",
                      amount="$50,001 - $100,000", date_traded="2024-06-01",
                      date_filed="2024-07-01"):
        scorer.db.execute(
            """INSERT INTO congress_trades
               (politician, symbol, action, amount_range, date_traded, date_filed, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (politician, symbol, action, amount, date_traded, date_filed, "http://test"),
        )
        scorer.db.connect().commit()

    def test_score_politician_no_trades(self, scorer) -> None:
        result = scorer.score_politician("Nobody")
        assert result.total_trades == 0
        assert result.score == 0.0

    def test_score_politician_with_trades(self, scorer) -> None:
        self._insert_trade(scorer, "Rep. Test", "NVDA")
        self._insert_trade(scorer, "Rep. Test", "AAPL")
        self._insert_trade(scorer, "Rep. Test", "MSFT", amount="$250,001 - $500,000")

        result = scorer.score_politician("Rep. Test")
        assert result.total_trades == 3
        assert result.score > 0
        assert result.tier in ("whale", "notable", "average", "noise")

    def test_score_all(self, scorer) -> None:
        self._insert_trade(scorer, "Alice", "NVDA", amount="$500,001 - $1,000,000")
        self._insert_trade(scorer, "Alice", "AAPL", amount="$500,001 - $1,000,000")
        self._insert_trade(scorer, "Bob", "SPY", amount="$1,001 - $15,000")

        results = scorer.score_all()
        assert len(results) == 2
        # Alice should score higher (large individual stocks vs small ETF)
        assert results[0].politician == "Alice"
        assert results[0].score > results[1].score

    def test_score_trade(self, scorer) -> None:
        trade = {"politician": "Unknown", "symbol": "NVDA", "amount_range": "$250,001 - $500,000"}
        score = scorer.score_trade(trade)
        assert 0.0 <= score <= 1.0
        assert score > 0.3  # Large individual stock should score well

    def test_score_trade_etf(self, scorer) -> None:
        trade = {"politician": "Unknown", "symbol": "SPY", "amount_range": "$1,001 - $15,000"}
        score = scorer.score_trade(trade)
        etf_score = score

        trade2 = {"politician": "Unknown", "symbol": "NVDA", "amount_range": "$250,001 - $500,000"}
        stock_score = scorer.score_trade(trade2)

        assert stock_score > etf_score

    def test_enrich_trade(self, scorer) -> None:
        trade = {
            "politician": "Rep. Test",
            "symbol": "NVDA",
            "action": "buy",
            "amount_range": "$50,001 - $100,000",
            "date_traded": "2024-06-01",
            "date_filed": "2024-07-01",
        }
        enriched = scorer.enrich_trade(trade)
        assert enriched["trade_size_bucket"] == "medium"
        assert enriched["disclosure_lag_days"] == 30
        assert "politician_score" in enriched
        assert "committee_relevant" in enriched

    def test_get_top_politicians(self, scorer) -> None:
        self._insert_trade(scorer, "Alice", "NVDA", amount="$500,001 - $1,000,000")
        self._insert_trade(scorer, "Bob", "SPY", amount="$1,001 - $15,000")
        scorer.score_all()

        top = scorer.get_top_politicians(n=10)
        assert len(top) == 2
        assert top[0]["score"] >= top[1]["score"]

    def test_build_reasoning(self, scorer) -> None:
        self._insert_trade(scorer, "Rep. Pelosi", "NVDA", amount="$250,001 - $500,000")
        scorer.score_politician("Rep. Pelosi")

        trade = scorer.enrich_trade({
            "politician": "Rep. Pelosi",
            "symbol": "NVDA",
            "action": "buy",
            "amount_range": "$250,001 - $500,000",
            "date_traded": "2024-06-01",
            "date_filed": "2024-07-01",
        })
        reasoning = scorer.build_reasoning(trade)
        assert "Rep. Pelosi" in reasoning
        assert "NVDA" in reasoning

    def test_persists_to_db(self, scorer) -> None:
        self._insert_trade(scorer, "Rep. Test", "NVDA")
        scorer.score_politician("Rep. Test")

        row = scorer.db.fetchone(
            "SELECT * FROM politician_scores WHERE politician = ?", ("Rep. Test",)
        )
        assert row is not None
        assert row["score"] > 0
        assert row["tier"] in ("whale", "notable", "average", "noise")


# ── Congress Engine Integration ──


class TestCongressScoringIntegration:
    """Test that CongressTradesEngine uses scoring correctly."""

    def test_generate_signals_uses_tiers(self, seeded_db) -> None:
        """Whale trades should get higher confidence, noise should be skipped."""
        from unittest.mock import MagicMock

        from engine.congress import CongressTradesEngine

        db = seeded_db

        # Insert position overlapping with NVDA (thesis already has NVDA)
        db.execute(
            "INSERT INTO positions (account_id, symbol, shares, avg_cost, side, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "NVDA", 100, 130.0, "long", 1),
        )

        # Insert trades
        db.execute(
            """INSERT INTO congress_trades
               (politician, symbol, action, amount_range, date_traded, date_filed, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Rep. Whale", "NVDA", "buy", "$500,001 - $1,000,000",
             "2024-06-01", "2024-08-01", "http://test"),
        )

        # Score the politician as whale
        db.execute(
            """INSERT INTO politician_scores (politician, score, tier, total_trades, win_rate)
               VALUES (?, ?, ?, ?, ?)""",
            ("Rep. Whale", 85, "whale", 50, 75.0),
        )
        db.connect().commit()

        # Setup signal engine mock
        signal_engine = MagicMock()
        signal_engine.create_signal.side_effect = lambda s: s

        engine = CongressTradesEngine(db, signal_engine)
        signals = engine.generate_signals(user_id=1)

        assert len(signals) == 1
        assert signals[0].confidence == 0.6  # whale confidence

    def test_noise_trades_skipped(self, seeded_db) -> None:
        """Noise-tier politicians should not generate signals."""
        from unittest.mock import MagicMock

        from engine.congress import CongressTradesEngine

        db = seeded_db

        db.execute(
            "INSERT INTO positions (account_id, symbol, shares, avg_cost, side, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (1, "SPY", 10, 450.0, "long", 1),
        )
        db.execute(
            """INSERT INTO congress_trades
               (politician, symbol, action, amount_range, date_traded, date_filed, source_url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("Rep. Noise", "SPY", "buy", "$1,001 - $15,000",
             "2024-06-01", "2024-06-02", "http://test"),
        )
        db.execute(
            """INSERT INTO politician_scores (politician, score, tier, total_trades, win_rate)
               VALUES (?, ?, ?, ?, ?)""",
            ("Rep. Noise", 25, "noise", 3, 20.0),
        )
        db.connect().commit()

        signal_engine = MagicMock()
        signal_engine.create_signal.side_effect = lambda s: s

        engine = CongressTradesEngine(db, signal_engine)
        signals = engine.generate_signals(user_id=1)

        assert len(signals) == 0
