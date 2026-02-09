"""Tests for the tax engine: lots, gains, harvesting, wash sales, account routing."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from engine.tax_engine import TaxEngine


@pytest.fixture
def tax_db(db):
    """Database with two accounts and tax lots."""
    # Create accounts
    db.execute(
        """INSERT INTO accounts (id, name, broker, account_type, account_hash, active, user_id)
           VALUES (1, 'Individual Brokerage', 'mock', 'individual_brokerage', '441', 1, 1)"""
    )
    db.execute(
        """INSERT INTO accounts (id, name, broker, account_type, account_hash, active, user_id)
           VALUES (2, 'Roth IRA', 'mock', 'roth_ira', '772', 1, 1)"""
    )
    db.connect().commit()
    return db


@pytest.fixture
def engine(tax_db):
    """TaxEngine with test database."""
    return TaxEngine(db=tax_db, user_id=1)


class TestTaxLotCRUD:
    """Tax lot creation and retrieval."""

    def test_create_tax_lot(self, engine):
        lot_id = engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)
        assert lot_id > 0

        lots = engine.get_tax_lots(account_id=1, symbol="NVDA")
        assert len(lots) == 1
        assert lots[0].symbol == "NVDA"
        assert lots[0].shares == 10
        assert lots[0].cost_basis == 150.0

    def test_multiple_lots_same_symbol(self, engine):
        engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)
        engine.create_tax_lot("NVDA", 5, 160.0, "2025-07-01", 1)

        lots = engine.get_tax_lots(account_id=1, symbol="NVDA")
        assert len(lots) == 2
        assert lots[0].acquired_date == "2025-06-01"  # FIFO order
        assert lots[1].acquired_date == "2025-07-01"

    def test_filter_by_account(self, engine):
        engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)
        engine.create_tax_lot("NVDA", 5, 100.0, "2024-01-01", 2)

        lots_1 = engine.get_tax_lots(account_id=1)
        lots_2 = engine.get_tax_lots(account_id=2)
        assert len(lots_1) == 1
        assert len(lots_2) == 1

    def test_filter_by_symbol(self, engine):
        engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)
        engine.create_tax_lot("AAPL", 20, 190.0, "2025-06-01", 1)

        lots = engine.get_tax_lots(symbol="AAPL")
        assert len(lots) == 1
        assert lots[0].symbol == "AAPL"


class TestShortTermLongTerm:
    """Short-term vs long-term classification based on 1-year threshold."""

    def test_short_term_lot(self, engine):
        recent = (datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 150.0, recent, 1)

        lots = engine.get_tax_lots(account_id=1)
        assert not lots[0].is_long_term

    def test_long_term_lot(self, engine):
        old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 150.0, old, 1)

        lots = engine.get_tax_lots(account_id=1)
        assert lots[0].is_long_term

    def test_boundary_exactly_one_year(self, engine):
        boundary = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 150.0, boundary, 1)

        lots = engine.get_tax_lots(account_id=1)
        assert lots[0].is_long_term


class TestUnrealizedGains:
    """Unrealized gain/loss calculations with current prices."""

    def test_unrealized_gain(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 1)

        lots = engine.get_tax_lots(current_prices={"NVDA": 150.0})
        assert lots[0].unrealized_gain == 500.0
        assert lots[0].unrealized_gain_pct == 50.0

    def test_unrealized_loss(self, engine):
        engine.create_tax_lot("NVDA", 10, 200.0, "2025-06-01", 1)

        lots = engine.get_tax_lots(current_prices={"NVDA": 150.0})
        assert lots[0].unrealized_gain == -500.0
        assert lots[0].unrealized_gain_pct == -25.0


class TestCalculateGains:
    """Per-account gain summary calculation."""

    def test_taxable_account_gains(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 1)
        summary = engine.calculate_gains(1, current_prices={"NVDA": 150.0})

        assert summary.account_id == 1
        assert summary.account_type == "individual_brokerage"
        assert summary.unrealized_st_gains == 500.0
        assert summary.estimated_tax_liability == 0  # No realized gains

    def test_realized_gains_after_sell(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 1)
        engine.sell_lots("NVDA", 10, 150.0, 1)

        summary = engine.calculate_gains(1)
        assert summary.realized_st_gains == 500.0
        assert summary.estimated_tax_liability == 185.0  # 500 * 0.37

    def test_ira_no_tax(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 2)
        summary = engine.calculate_gains(2, current_prices={"NVDA": 150.0})

        assert summary.account_type == "roth_ira"
        assert summary.estimated_tax_liability == 0


class TestSellLots:
    """FIFO lot matching on sell."""

    def test_fifo_sell(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-01-01", 1)
        engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)

        consumed = engine.sell_lots("NVDA", 10, 200.0, 1, method="fifo")
        assert len(consumed) == 1
        assert consumed[0]["cost_per_share"] == 100.0  # Oldest lot
        assert consumed[0]["gain"] == 1000.0

    def test_lifo_sell(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-01-01", 1)
        engine.create_tax_lot("NVDA", 10, 150.0, "2025-06-01", 1)

        consumed = engine.sell_lots("NVDA", 10, 200.0, 1, method="lifo")
        assert len(consumed) == 1
        assert consumed[0]["cost_per_share"] == 150.0  # Newest lot
        assert consumed[0]["gain"] == 500.0

    def test_partial_sell(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-01-01", 1)

        consumed = engine.sell_lots("NVDA", 5, 150.0, 1)
        assert consumed[0]["shares"] == 5

        # Remaining lot should have 5 shares
        lots = engine.get_tax_lots(account_id=1, symbol="NVDA")
        remaining_shares = sum(lot.shares for lot in lots)
        assert remaining_shares == 5

    def test_sell_across_lots(self, engine):
        engine.create_tax_lot("NVDA", 5, 100.0, "2025-01-01", 1)
        engine.create_tax_lot("NVDA", 5, 150.0, "2025-06-01", 1)

        consumed = engine.sell_lots("NVDA", 8, 200.0, 1)
        assert len(consumed) == 2
        assert consumed[0]["shares"] == 5  # All of first lot
        assert consumed[1]["shares"] == 3  # Partial second lot


class TestHarvestCandidates:
    """Tax-loss harvesting detection."""

    def test_finds_losses_in_taxable(self, engine):
        engine.create_tax_lot("AMD", 20, 180.0, "2025-06-01", 1)

        candidates = engine.find_harvest_candidates(
            min_loss=100, current_prices={"AMD": 150.0}
        )
        assert len(candidates) == 1
        assert candidates[0].symbol == "AMD"
        assert candidates[0].unrealized_loss == -600.0
        assert candidates[0].suggested_replacement == "NVDA"

    def test_ignores_ira_positions(self, engine):
        engine.create_tax_lot("AMD", 20, 180.0, "2025-06-01", 2)  # Roth IRA

        candidates = engine.find_harvest_candidates(
            min_loss=100, current_prices={"AMD": 150.0}
        )
        assert len(candidates) == 0

    def test_detects_wash_sale_risk(self, engine):
        engine.create_tax_lot("AMD", 20, 180.0, "2025-06-01", 1)  # Taxable
        engine.create_tax_lot("AMD", 10, 140.0, "2025-06-15", 2)  # Roth IRA

        candidates = engine.find_harvest_candidates(
            min_loss=100, current_prices={"AMD": 150.0}
        )
        assert len(candidates) == 1
        assert candidates[0].wash_sale_risk is True

    def test_below_threshold_excluded(self, engine):
        engine.create_tax_lot("AMD", 5, 105.0, "2025-06-01", 1)

        candidates = engine.find_harvest_candidates(
            min_loss=500, min_loss_pct=5.0, current_prices={"AMD": 100.0}
        )
        # Loss = $25, 4.76% — below both thresholds
        assert len(candidates) == 0


class TestWashSaleDetection:
    """Wash sale detection across all accounts including IRAs."""

    def test_no_wash_sale(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2024-01-01", 1)

        result = engine.check_wash_sale("NVDA", "2025-06-01")
        assert not result.is_wash_sale

    def test_wash_sale_same_account(self, engine):
        today = datetime.now().strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 100.0, today, 1)

        result = engine.check_wash_sale("NVDA", today)
        assert result.is_wash_sale
        assert "NVDA" in result.warning

    def test_wash_sale_cross_account_ira(self, engine):
        """Buying in IRA within 30 days of selling at loss in taxable = wash sale."""
        today = datetime.now().strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 100.0, today, 2)  # Bought in Roth IRA

        result = engine.check_wash_sale("NVDA", today)
        assert result.is_wash_sale

    def test_wash_sale_outside_window(self, engine):
        old = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 100.0, old, 2)

        today = datetime.now().strftime("%Y-%m-%d")
        result = engine.check_wash_sale("NVDA", today)
        assert not result.is_wash_sale

    def test_sell_at_loss_adds_to_watchlist(self, engine):
        engine.create_tax_lot("NVDA", 10, 200.0, "2025-01-01", 1)

        engine.sell_lots("NVDA", 10, 150.0, 1)

        watchlist = engine.db.fetchall(
            "SELECT * FROM wash_sale_watchlist WHERE symbol = 'NVDA'"
        )
        assert len(watchlist) == 1
        assert watchlist[0]["loss_amount"] == 500.0


class TestAccountRecommendation:
    """Account routing logic: growth → Roth, dividends → Traditional, etc."""

    def test_short_term_goes_to_roth(self, engine):
        from engine import Signal, SignalAction, SignalSource
        signal = Signal(
            action=SignalAction.BUY, symbol="CRWD",
            horizon="3m", source=SignalSource.MANUAL,
        )
        rec = engine.recommend_account(signal)
        assert rec.account_type == "roth_ira"
        assert "short-term" in rec.reasoning.lower()

    def test_default_prefers_roth(self, engine):
        from engine import Signal, SignalAction, SignalSource
        signal = Signal(
            action=SignalAction.BUY, symbol="NVDA",
            horizon="2y", source=SignalSource.MANUAL,
        )
        rec = engine.recommend_account(signal)
        assert rec.account_type == "roth_ira"

    def test_no_roth_falls_to_taxable(self, engine):
        # Remove Roth account
        engine.db.execute("DELETE FROM accounts WHERE account_type = 'roth_ira'")
        engine.db.connect().commit()

        from engine import Signal, SignalAction, SignalSource
        signal = Signal(
            action=SignalAction.BUY, symbol="NVDA",
            source=SignalSource.MANUAL,
        )
        rec = engine.recommend_account(signal)
        assert rec.account_type == "individual_brokerage"

    def test_no_accounts_returns_none(self, engine):
        engine.db.execute("DELETE FROM accounts")
        engine.db.connect().commit()

        from engine import Signal, SignalAction, SignalSource
        signal = Signal(
            action=SignalAction.BUY, symbol="NVDA",
            source=SignalSource.MANUAL,
        )
        rec = engine.recommend_account(signal)
        assert rec.account_id == 0


class TestTaxImpact:
    """Tax impact estimation for selling."""

    def test_taxable_short_term(self, engine):
        recent = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 100.0, recent, 1)

        impact = engine.estimate_tax_impact("NVDA", 10, 1, 150.0)
        assert impact.realized_gain == 500.0
        assert not impact.is_long_term
        assert impact.estimated_tax == 185.0  # 500 * 0.37
        assert impact.effective_rate == 0.37

    def test_taxable_long_term(self, engine):
        old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
        engine.create_tax_lot("NVDA", 10, 100.0, old, 1)

        impact = engine.estimate_tax_impact("NVDA", 10, 1, 150.0)
        assert impact.is_long_term
        assert impact.estimated_tax == 100.0  # 500 * 0.20
        assert impact.effective_rate == 0.20

    def test_roth_ira_no_tax(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 2)

        impact = engine.estimate_tax_impact("NVDA", 10, 2, 150.0)
        assert impact.estimated_tax == 0
        assert impact.effective_rate == 0

    def test_loss_no_negative_tax(self, engine):
        engine.create_tax_lot("NVDA", 10, 200.0, "2025-06-01", 1)

        impact = engine.estimate_tax_impact("NVDA", 10, 1, 150.0)
        assert impact.realized_gain == -500.0
        assert impact.estimated_tax == 0  # No negative tax


class TestAccountSummary:
    """Per-account summary generation."""

    def test_summary_both_accounts(self, engine):
        engine.create_tax_lot("NVDA", 10, 100.0, "2025-06-01", 1)
        engine.create_tax_lot("MSFT", 5, 400.0, "2024-01-01", 2)

        summaries = engine.get_account_summary(
            current_prices={"NVDA": 150.0, "MSFT": 450.0}
        )
        assert len(summaries) == 2

        brokerage = next(s for s in summaries if s.account_type == "individual_brokerage")
        assert brokerage.total_value == 1500.0
        assert brokerage.total_cost_basis == 1000.0
        assert brokerage.unrealized_gain == 500.0
