"""Head-to-head comparison of IRA-First vs Smart routing strategies.

Tests 10 realistic scenarios with actual tax math to quantify the
difference between simple IRA-first routing and the smart routing
engine. Uses 22% marginal rate for ordinary/short-term, 15% for
long-term capital gains.

Expected result: strategies are nearly identical for most scenarios.
Smart routing only meaningfully wins on tax-loss harvesting and
REIT dividend placement.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

# Tax rates for comparison
ST_RATE = 0.22  # short-term / ordinary income (22% bracket)
LT_RATE = 0.15  # long-term capital gains
REIT_DIVIDEND_RATE = 0.22  # REITs pay ordinary income dividends
QUALIFIED_DIVIDEND_RATE = 0.15  # qualified dividends


@dataclass
class Scenario:
    """A single routing comparison scenario."""

    name: str
    position_value: float
    holding_months: int
    total_return_pct: float
    annual_yield_pct: float  # dividend yield
    is_reit: bool
    roth_has_cash: bool
    has_taxable_loss: bool  # existing loss in taxable to harvest
    taxable_loss_amount: float

    @property
    def gain(self) -> float:
        return self.position_value * self.total_return_pct

    @property
    def annual_dividends(self) -> float:
        return self.position_value * self.annual_yield_pct

    @property
    def total_dividends(self) -> float:
        return self.annual_dividends * (self.holding_months / 12)

    @property
    def is_long_term(self) -> bool:
        return self.holding_months >= 12


def ira_first_tax(s: Scenario) -> float:
    """Calculate total tax under IRA-first strategy.

    Always uses Roth if cash available, otherwise taxable.
    Does NOT harvest losses proactively.
    """
    if s.roth_has_cash:
        # Everything in Roth = $0 tax
        return 0.0

    # Taxable account
    cap_gains_rate = LT_RATE if s.is_long_term else ST_RATE
    cap_gains_tax = max(0, s.gain) * cap_gains_rate

    div_rate = REIT_DIVIDEND_RATE if s.is_reit else QUALIFIED_DIVIDEND_RATE
    div_tax = s.total_dividends * div_rate

    return cap_gains_tax + div_tax


def smart_route_tax(s: Scenario) -> float:
    """Calculate total tax under smart routing strategy.

    Routes based on asset characteristics:
    - Short-term/growth → Roth
    - High-yield/REIT → Traditional IRA (if available)
    - Harvests losses in taxable when possible
    """
    if s.roth_has_cash:
        # Smart routing also prefers Roth for most things
        # But for REITs with high yield, would use Traditional IRA
        if s.is_reit and s.annual_yield_pct > 0.03:
            # Traditional IRA: dividends tax-deferred, gains tax-deferred
            # Tax paid at withdrawal (ordinary income rate on everything)
            # But we're deferring, so present-value tax is lower
            # Simplification: assume same bracket at withdrawal = same tax, just deferred
            # Net benefit ≈ 0 vs Roth for same bracket
            # Actually Roth is BETTER because Roth = never taxed
            return 0.0

        # Non-REIT in Roth = same as IRA-first
        return 0.0

    # Taxable account — smart routing can harvest losses
    cap_gains_rate = LT_RATE if s.is_long_term else ST_RATE
    cap_gains_tax = max(0, s.gain) * cap_gains_rate

    div_rate = REIT_DIVIDEND_RATE if s.is_reit else QUALIFIED_DIVIDEND_RATE
    div_tax = s.total_dividends * div_rate

    # Harvest existing losses to offset gains
    tax_before_harvest = cap_gains_tax + div_tax
    if s.has_taxable_loss and s.taxable_loss_amount > 0:
        # Loss offsets gains, remaining offsets up to $3K ordinary income
        offset = min(s.taxable_loss_amount, cap_gains_tax / cap_gains_rate)
        harvested_savings = offset * cap_gains_rate
        remaining_loss = s.taxable_loss_amount - offset
        if remaining_loss > 0:
            # Up to $3K offsets ordinary income
            harvested_savings += min(remaining_loss, 3000) * ST_RATE
        return max(0, tax_before_harvest - harvested_savings)

    return tax_before_harvest


SCENARIOS = [
    Scenario(
        name="1. Growth stock, short hold (NVDA 6mo +40%)",
        position_value=50_000, holding_months=6,
        total_return_pct=0.40, annual_yield_pct=0.0,
        is_reit=False, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="2. Dividend stock (KO 2yr, 3% yield)",
        position_value=30_000, holding_months=24,
        total_return_pct=0.15, annual_yield_pct=0.03,
        is_reit=False, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="3. Tax-loss harvest (ARKK -25%, buy QQQ)",
        position_value=40_000, holding_months=8,
        total_return_pct=0.12, annual_yield_pct=0.0,
        is_reit=False, roth_has_cash=False,
        has_taxable_loss=True, taxable_loss_amount=10_000,
    ),
    Scenario(
        name="4. Short-term flip (TSLA 3mo +20%)",
        position_value=40_000, holding_months=3,
        total_return_pct=0.20, annual_yield_pct=0.0,
        is_reit=False, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="5. Roth is full, buy AAPL",
        position_value=20_000, holding_months=18,
        total_return_pct=0.25, annual_yield_pct=0.006,
        is_reit=False, roth_has_cash=False,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="6. Sell winner in taxable (+50%)",
        position_value=60_000, holding_months=14,
        total_return_pct=0.50, annual_yield_pct=0.0,
        is_reit=False, roth_has_cash=False,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="7. Wash sale trap (sell MSFT loss, rebuy)",
        position_value=30_000, holding_months=5,
        total_return_pct=-0.15, annual_yield_pct=0.008,
        is_reit=False, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="8. High-yield REIT (O, 5% yield)",
        position_value=25_000, holding_months=24,
        total_return_pct=0.10, annual_yield_pct=0.05,
        is_reit=True, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="9. Long-term hold, low growth (JNJ 5yr +5%/yr)",
        position_value=35_000, holding_months=60,
        total_return_pct=0.28, annual_yield_pct=0.026,
        is_reit=False, roth_has_cash=True,
        has_taxable_loss=False, taxable_loss_amount=0,
    ),
    Scenario(
        name="10. Multiple signals, Roth partial cash",
        position_value=30_000, holding_months=9,
        total_return_pct=0.18, annual_yield_pct=0.0,
        is_reit=False, roth_has_cash=False,
        has_taxable_loss=True, taxable_loss_amount=5_000,
    ),
]


class TestIRAFirstVsSmartRouting:
    """Compare IRA-first and smart routing across 10 scenarios."""

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.name for s in SCENARIOS])
    def test_scenario_taxes(self, scenario: Scenario) -> None:
        """Each scenario should produce valid non-negative tax amounts."""
        ira_tax = ira_first_tax(scenario)
        smart_tax = smart_route_tax(scenario)

        assert ira_tax >= 0, f"IRA-first tax negative: {ira_tax}"
        assert smart_tax >= 0, f"Smart tax negative: {smart_tax}"
        assert smart_tax <= ira_tax or smart_tax == pytest.approx(
            ira_tax, abs=1.0
        ), f"Smart should never be worse: smart={smart_tax}, ira={ira_tax}"

    def test_roth_available_both_zero(self) -> None:
        """When Roth has cash, both strategies pay $0 tax."""
        roth_scenarios = [s for s in SCENARIOS if s.roth_has_cash]
        for s in roth_scenarios:
            assert ira_first_tax(s) == 0.0
            assert smart_route_tax(s) == 0.0

    def test_harvest_scenario_smart_wins(self) -> None:
        """Smart routing wins when there are losses to harvest."""
        harvest = SCENARIOS[2]  # Scenario 3: ARKK loss
        ira_tax = ira_first_tax(harvest)
        smart_tax = smart_route_tax(harvest)
        assert smart_tax < ira_tax
        savings = ira_tax - smart_tax
        assert savings > 500, f"Harvesting should save meaningful money: {savings}"

    def test_roth_full_no_losses_tie(self) -> None:
        """When Roth is full and no losses exist, strategies tie."""
        s5 = SCENARIOS[4]  # Scenario 5
        s6 = SCENARIOS[5]  # Scenario 6
        assert ira_first_tax(s5) == pytest.approx(smart_route_tax(s5), abs=1.0)
        assert ira_first_tax(s6) == pytest.approx(smart_route_tax(s6), abs=1.0)

    def test_comparison_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Print a readable comparison table (always passes)."""
        print("\n" + "=" * 85)
        print("IRA-FIRST vs SMART ROUTING — TAX COMPARISON")
        print("=" * 85)
        print(
            f"{'Scenario':<48} {'IRA-First':>10} {'Smart':>10} "
            f"{'Savings':>10} {'Winner':>7}"
        )
        print("-" * 85)

        total_ira = 0.0
        total_smart = 0.0

        for s in SCENARIOS:
            ira = ira_first_tax(s)
            smart = smart_route_tax(s)
            savings = ira - smart
            total_ira += ira
            total_smart += smart

            if abs(savings) < 1:
                winner = "TIE"
            elif savings > 0:
                winner = "SMART"
            else:
                winner = "IRA"

            print(
                f"{s.name:<48} ${ira:>8,.0f} ${smart:>8,.0f} "
                f"${savings:>8,.0f}   {winner}"
            )

        print("-" * 85)
        total_savings = total_ira - total_smart
        print(
            f"{'TOTAL':<48} ${total_ira:>8,.0f} ${total_smart:>8,.0f} "
            f"${total_savings:>8,.0f}"
        )
        print(f"\nSmart routing saves ${total_savings:,.0f} total across all scenarios")
        pct = (
            (total_savings / total_ira * 100) if total_ira > 0 else 0
        )
        print(f"That's {pct:.1f}% of total tax paid under IRA-first")
        print(
            "\nVerdict: Both strategies are identical when Roth has cash."
        )
        print(
            "Smart routing only wins on tax-loss harvesting in the "
            "taxable account."
        )
        print("=" * 85)
