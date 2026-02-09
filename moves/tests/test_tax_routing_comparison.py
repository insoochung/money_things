"""Head-to-head comparison of IRA-First vs Smart routing strategies.

Tests 10 realistic scenarios with actual tax math. Uses:
- 22% marginal rate for ordinary income / short-term cap gains
- 15% for long-term capital gains
- 22% for REIT dividends (ordinary income, not qualified)
- 15% for qualified dividends

Implements both routing strategies as pure functions, calculates tax
outcomes, and prints a comparison table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from db.database import Database
from engine.tax_engine import TaxEngine

# ── Tax rates ──
ST_RATE = 0.22  # short-term capital gains / ordinary income
LT_RATE = 0.15  # long-term capital gains
REIT_DIV_RATE = 0.22  # REIT dividends = ordinary income
QUAL_DIV_RATE = 0.15  # qualified dividends
ORDINARY_RATE = 0.22  # ordinary income (Traditional IRA withdrawals)


@dataclass
class Account:
    """Simplified account for routing."""

    id: int
    name: str
    account_type: str  # roth_ira, traditional_ira, individual_brokerage
    cash: float


@dataclass
class RoutingSignal:
    """Simplified signal for routing decisions."""

    symbol: str
    action: str  # BUY or SELL
    amount: float
    horizon_months: int
    annual_yield_pct: float
    is_reit: bool
    is_wash_sale_risk: bool  # same symbol recently sold at loss


@dataclass
class Scenario:
    """A complete routing comparison scenario."""

    name: str
    position_value: float
    holding_months: int
    total_return_pct: float
    annual_yield_pct: float
    is_reit: bool
    roth_cash: float
    trad_ira_cash: float
    has_taxable_loss: bool
    taxable_loss_amount: float
    is_wash_sale_risk: bool = False
    extra_signals: int = 0  # additional signals competing for Roth cash
    notes: str = ""

    @property
    def gain(self) -> float:
        return self.position_value * self.total_return_pct

    @property
    def total_dividends(self) -> float:
        return self.position_value * self.annual_yield_pct * (self.holding_months / 12)

    @property
    def is_long_term(self) -> bool:
        return self.holding_months >= 12


@dataclass
class TaxOutcome:
    """Tax result for a strategy on a scenario."""

    cap_gains_tax: float = 0.0
    dividend_tax: float = 0.0
    harvest_savings: float = 0.0
    account_used: str = ""
    notes: str = ""

    @property
    def total_tax(self) -> float:
        return max(0, self.cap_gains_tax + self.dividend_tax - self.harvest_savings)

    @property
    def net_return(self) -> float:
        return -self.total_tax  # relative to gross return


# ── Strategy implementations ──


def ira_first_route(s: Scenario) -> TaxOutcome:
    """IRA-first: always Roth, overflow to taxable. No loss harvesting."""
    if s.roth_cash >= s.position_value:
        return TaxOutcome(account_used="Roth IRA", notes="Tax-free")

    # Roth can't cover it → taxable
    rate = LT_RATE if s.is_long_term else ST_RATE
    cap_tax = max(0, s.gain) * rate

    div_rate = REIT_DIV_RATE if s.is_reit else QUAL_DIV_RATE
    div_tax = s.total_dividends * div_rate

    return TaxOutcome(
        cap_gains_tax=cap_tax,
        dividend_tax=div_tax,
        account_used="Taxable",
        notes="Roth unavailable/insufficient",
    )


def smart_route(s: Scenario) -> TaxOutcome:
    """Smart routing: asset-aware placement + loss harvesting.

    Rules:
    - Short-term/growth → Roth (avoid short-term cap gains)
    - REIT/high-yield → Traditional IRA (defer ordinary income dividends)
    - Harvest losses in taxable when available
    - Wash sale guard: block if same symbol sold at loss recently
    """
    # Wash sale blocked → can't use Roth for this symbol
    if s.is_wash_sale_risk:
        return TaxOutcome(
            account_used="BLOCKED",
            notes="Wash sale — trade blocked across all accounts",
        )

    # REIT with high yield: Traditional IRA defers ordinary-income dividends,
    # but Roth is BETTER when available (never taxed > tax-deferred).
    # Only use Traditional for REITs when Roth has no cash.
    if (s.is_reit and s.annual_yield_pct >= 0.03
            and s.trad_ira_cash >= s.position_value
            and s.roth_cash < s.position_value):
        holding_years = s.holding_months / 12
        annual_div_tax_saved = s.position_value * s.annual_yield_pct * REIT_DIV_RATE
        deferral_benefit = annual_div_tax_saved * holding_years * 0.05 * (holding_years / 2)
        withdrawal_penalty = max(0, s.gain) * (ORDINARY_RATE - LT_RATE)

        return TaxOutcome(
            cap_gains_tax=withdrawal_penalty,
            dividend_tax=0,
            harvest_savings=deferral_benefit,
            account_used="Traditional IRA",
            notes="REIT dividends deferred; Roth unavailable",
        )

    # Roth has cash → use it
    if s.roth_cash >= s.position_value:
        # But also harvest losses if available in taxable
        harvest = 0.0
        if s.has_taxable_loss and s.taxable_loss_amount > 0:
            # Harvest the loss separately (sell loser in taxable)
            cap_rate = LT_RATE if s.is_long_term else ST_RATE
            harvest = min(s.taxable_loss_amount, 3000) * ST_RATE
            if s.taxable_loss_amount > 3000:
                harvest += (s.taxable_loss_amount - 3000) * cap_rate * 0.5

        return TaxOutcome(
            harvest_savings=harvest,
            account_used="Roth IRA",
            notes="Tax-free + harvested taxable losses" if harvest > 0 else "Tax-free",
        )

    # No Roth cash → taxable, but harvest losses
    rate = LT_RATE if s.is_long_term else ST_RATE
    cap_tax = max(0, s.gain) * rate

    div_rate = REIT_DIV_RATE if s.is_reit else QUAL_DIV_RATE
    div_tax = s.total_dividends * div_rate

    harvest = 0.0
    if s.has_taxable_loss and s.taxable_loss_amount > 0:
        # Offset gains with harvested losses
        loss_offset = min(s.taxable_loss_amount, max(0, s.gain))
        harvest = loss_offset * rate
        remaining = s.taxable_loss_amount - loss_offset
        if remaining > 0:
            harvest += min(remaining, 3000) * ST_RATE

    return TaxOutcome(
        cap_gains_tax=cap_tax,
        dividend_tax=div_tax,
        harvest_savings=harvest,
        account_used="Taxable",
        notes="Harvested losses" if harvest > 0 else "No Roth cash",
    )


# ── Scenarios ──

SCENARIOS = [
    Scenario(
        name="1. Growth stock short hold (NVDA 6mo +40%)",
        position_value=50_000, holding_months=6,
        total_return_pct=0.40, annual_yield_pct=0.0,
        is_reit=False, roth_cash=100_000, trad_ira_cash=50_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both use Roth → tie",
    ),
    Scenario(
        name="2. Dividend stock (KO 2yr 3% yield)",
        position_value=30_000, holding_months=24,
        total_return_pct=0.15, annual_yield_pct=0.03,
        is_reit=False, roth_cash=100_000, trad_ira_cash=50_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both Roth → tie (qualified divs tax-free in Roth)",
    ),
    Scenario(
        name="3. Tax-loss harvest (ARKK -25%, buy QQQ)",
        position_value=40_000, holding_months=8,
        total_return_pct=0.12, annual_yield_pct=0.0,
        is_reit=False, roth_cash=0, trad_ira_cash=0,
        has_taxable_loss=True, taxable_loss_amount=10_000,
        notes="Smart harvests $10K loss → saves tax",
    ),
    Scenario(
        name="4. Short-term flip (TSLA 3mo +20%)",
        position_value=40_000, holding_months=3,
        total_return_pct=0.20, annual_yield_pct=0.0,
        is_reit=False, roth_cash=100_000, trad_ira_cash=50_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both Roth → tie",
    ),
    Scenario(
        name="5. Roth is full (buy AAPL $20K)",
        position_value=20_000, holding_months=18,
        total_return_pct=0.25, annual_yield_pct=0.006,
        is_reit=False, roth_cash=0, trad_ira_cash=0,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both taxable → tie",
    ),
    Scenario(
        name="6. Sell winner in taxable (+50%)",
        position_value=60_000, holding_months=14,
        total_return_pct=0.50, annual_yield_pct=0.0,
        is_reit=False, roth_cash=0, trad_ira_cash=0,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both taxable, no losses → tie",
    ),
    Scenario(
        name="7. Wash sale trap (MSFT loss, rebuy)",
        position_value=30_000, holding_months=5,
        total_return_pct=-0.15, annual_yield_pct=0.008,
        is_reit=False, roth_cash=100_000, trad_ira_cash=50_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        is_wash_sale_risk=True,
        notes="Both should block",
    ),
    Scenario(
        name="8. High-yield REIT (O 5% yield 2yr)",
        position_value=25_000, holding_months=24,
        total_return_pct=0.10, annual_yield_pct=0.05,
        is_reit=True, roth_cash=100_000, trad_ira_cash=100_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both Roth (never-taxed > deferred for same bracket)",
    ),
    Scenario(
        name="9. Long-term low growth (JNJ 5yr +28%)",
        position_value=35_000, holding_months=60,
        total_return_pct=0.28, annual_yield_pct=0.026,
        is_reit=False, roth_cash=100_000, trad_ira_cash=50_000,
        has_taxable_loss=False, taxable_loss_amount=0,
        notes="Both Roth → tie",
    ),
    Scenario(
        name="10. Multiple signals, Roth partial",
        position_value=30_000, holding_months=9,
        total_return_pct=0.18, annual_yield_pct=0.0,
        is_reit=False, roth_cash=0, trad_ira_cash=0,
        has_taxable_loss=True, taxable_loss_amount=5_000,
        notes="Smart harvests $5K loss in taxable",
    ),
]


# ── Tests ──


class TestScenarioTaxMath:
    """Verify tax calculations are correct for each scenario."""

    def test_scenario_1_growth_short_roth_tie(self) -> None:
        s = SCENARIOS[0]
        assert ira_first_route(s).total_tax == 0
        assert smart_route(s).total_tax == 0

    def test_scenario_2_dividend_roth_tie(self) -> None:
        s = SCENARIOS[1]
        assert ira_first_route(s).total_tax == 0
        assert smart_route(s).total_tax == 0

    def test_scenario_3_harvest_smart_wins(self) -> None:
        s = SCENARIOS[2]
        ira = ira_first_route(s)
        smart = smart_route(s)

        # IRA-first: taxable, 8mo hold = short-term
        # Gain = 40K * 0.12 = $4,800, tax = $4,800 * 0.22 = $1,056
        assert ira.cap_gains_tax == pytest.approx(1056, abs=1)
        assert ira.harvest_savings == 0

        # Smart: same gains tax but harvests $10K loss
        # Loss offsets $4,800 gain → saves $4,800 * 0.22 = $1,056
        # Remaining $5,200 loss: $3,000 offsets ordinary → $660
        assert smart.harvest_savings > 1000
        assert smart.total_tax < ira.total_tax

    def test_scenario_4_short_flip_roth_tie(self) -> None:
        s = SCENARIOS[3]
        assert ira_first_route(s).total_tax == 0
        assert smart_route(s).total_tax == 0

    def test_scenario_5_roth_full_tie(self) -> None:
        s = SCENARIOS[4]
        ira = ira_first_route(s)
        smart = smart_route(s)
        # 18mo = long-term, gain = $5K, tax = $5K * 0.15 = $750
        assert ira.cap_gains_tax == pytest.approx(750, abs=1)
        assert ira.total_tax == pytest.approx(smart.total_tax, abs=1)

    def test_scenario_6_sell_winner_tie(self) -> None:
        s = SCENARIOS[5]
        ira = ira_first_route(s)
        smart = smart_route(s)
        # 14mo = long-term, gain = $30K, tax = $30K * 0.15 = $4,500
        assert ira.cap_gains_tax == pytest.approx(4500, abs=1)
        assert ira.total_tax == pytest.approx(smart.total_tax, abs=1)

    def test_scenario_7_wash_sale_blocked(self) -> None:
        s = SCENARIOS[6]
        ira = ira_first_route(s)
        smart = smart_route(s)
        # IRA-first doesn't check wash sales in the pure function
        # Smart route blocks the trade
        assert smart.account_used == "BLOCKED"
        # IRA-first still routes to Roth (wash sale check is separate)
        assert ira.account_used == "Roth IRA"

    def test_scenario_8_reit_both_roth(self) -> None:
        s = SCENARIOS[7]
        ira = ira_first_route(s)
        smart = smart_route(s)
        # Both use Roth when it has cash — Roth (never taxed) beats
        # Traditional (deferred) for REITs in the same tax bracket.
        assert ira.total_tax == 0
        assert smart.total_tax == 0
        assert smart.account_used == "Roth IRA"

    def test_scenario_9_long_term_roth_tie(self) -> None:
        s = SCENARIOS[8]
        assert ira_first_route(s).total_tax == 0
        assert smart_route(s).total_tax == 0

    def test_scenario_10_partial_roth_smart_harvests(self) -> None:
        s = SCENARIOS[9]
        ira = ira_first_route(s)
        smart = smart_route(s)
        # Both in taxable, gain = $5,400, 9mo = short-term
        assert ira.cap_gains_tax == pytest.approx(1188, abs=1)
        assert smart.harvest_savings > 500
        assert smart.total_tax < ira.total_tax


class TestWashSaleGuard:
    """Wash sale detection works in both strategies."""

    def test_smart_blocks_wash_sale(self) -> None:
        s = Scenario(
            name="wash sale", position_value=10_000, holding_months=1,
            total_return_pct=0.05, annual_yield_pct=0,
            is_reit=False, roth_cash=50_000, trad_ira_cash=0,
            has_taxable_loss=False, taxable_loss_amount=0,
            is_wash_sale_risk=True,
        )
        result = smart_route(s)
        assert result.account_used == "BLOCKED"
        assert "wash sale" in result.notes.lower()

    def test_wash_sale_engine_integration(self, db: Database) -> None:
        """TaxEngine.check_wash_sale catches cross-account wash sales."""
        db.execute(
            """INSERT INTO accounts (id, name, broker, account_type, active, user_id)
               VALUES (1, 'Brokerage', 'mock', 'individual_brokerage', 1, 1)"""
        )
        db.execute(
            """INSERT INTO accounts (id, name, broker, account_type, active, user_id)
               VALUES (2, 'Roth', 'mock', 'roth_ira', 1, 1)"""
        )
        db.connect().commit()

        engine = TaxEngine(db=db, user_id=1)
        today = datetime.now().strftime("%Y-%m-%d")

        # Buy MSFT in Roth today
        engine.create_tax_lot("MSFT", 10, 400.0, today, 2)

        # Selling MSFT in taxable should flag wash sale
        result = engine.check_wash_sale("MSFT", today)
        assert result.is_wash_sale
        assert 2 in {b["account_id"] for b in result.conflicting_buys}


class TestStrategyProperties:
    """General properties that both strategies should satisfy."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
    def test_non_negative_tax(self, scenario: Scenario) -> None:
        """Tax should never be negative."""
        assert ira_first_route(scenario).total_tax >= 0
        assert smart_route(scenario).total_tax >= 0

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
    def test_roth_means_zero_tax(self, scenario: Scenario) -> None:
        """If routed to Roth, cap gains + dividend tax should be zero."""
        for result in [ira_first_route(scenario), smart_route(scenario)]:
            if result.account_used == "Roth IRA":
                assert result.cap_gains_tax == 0
                assert result.dividend_tax == 0

    def test_smart_never_worse_when_no_wash(self) -> None:
        """Smart routing should never pay MORE tax than IRA-first (excl. wash)."""
        for s in SCENARIOS:
            if s.is_wash_sale_risk:
                continue  # wash sale blocking is a feature, not a cost
            ira = ira_first_route(s).total_tax
            smart = smart_route(s).total_tax
            assert smart <= ira + 1, (
                f"{s.name}: smart={smart:.0f} > ira={ira:.0f}"
            )


class TestComparisonSummary:
    """Print a readable comparison table."""

    def test_strategy_comparison_summary(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Print a comparison table. Always passes — the value is the output."""
        rows: list[tuple[str, float, float, float, str]] = []

        for s in SCENARIOS:
            ira = ira_first_route(s)
            smart = smart_route(s)
            ira_tax = ira.total_tax
            smart_tax = smart.total_tax
            savings = ira_tax - smart_tax

            if smart.account_used == "BLOCKED":
                winner = "GUARD"
            elif abs(savings) < 1:
                winner = "TIE"
            elif savings > 0:
                winner = "SMART"
            else:
                winner = "IRA"

            rows.append((s.name, ira_tax, smart_tax, savings, winner))

        # Print table
        hdr = (
            f"{'Scenario':<45} {'IRA-1st':>9} {'Smart':>9} "
            f"{'Saved':>9} {'Winner':>6}"
        )
        sep = "-" * len(hdr)

        print("\n" + "=" * len(hdr))
        print("IRA-FIRST vs SMART ROUTING — TAX COMPARISON")
        print(
            "Rates: 22% ordinary/ST, 15% LT cap gains, "
            "22% REIT divs, 15% qual divs"
        )
        print("=" * len(hdr))
        print(hdr)
        print(sep)

        total_ira = 0.0
        total_smart = 0.0

        for name, ira_tax, smart_tax, savings, winner in rows:
            total_ira += ira_tax
            total_smart += smart_tax
            print(
                f"{name:<45} ${ira_tax:>7,.0f} ${smart_tax:>7,.0f} "
                f"${savings:>7,.0f}  {winner:>5}"
            )

        print(sep)
        total_saved = total_ira - total_smart
        print(
            f"{'TOTAL':<45} ${total_ira:>7,.0f} ${total_smart:>7,.0f} "
            f"${total_saved:>7,.0f}"
        )

        pct = (total_saved / total_ira * 100) if total_ira > 0 else 0
        print(f"\nSmart routing saves ${total_saved:,.0f} ({pct:.1f}% of IRA-first tax)")
        print()
        print("Key takeaways:")
        print("  • When Roth has cash, BOTH strategies pay $0 — no difference")
        print("  • Smart routing wins ONLY on tax-loss harvesting scenarios")
        print(
            "  • REIT→Traditional is debatable (deferral vs never-taxed Roth)"
        )
        print("  • Wash sale guard is essential in both strategies")
        print(
            "  • Conclusion: IRA-first + harvesting scanner = best simple approach"
        )
        print("=" * len(hdr))

        # This test always passes — the value is the printed comparison
        assert total_ira >= 0
        assert total_smart >= 0
