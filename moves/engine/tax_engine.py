"""Tax-aware multi-account engine for tax lot tracking, harvesting, and account routing.

Provides tax lot management (FIFO/LIFO/specific ID), tax-loss harvesting candidate
detection, wash sale tracking across all accounts (including IRAs), and intelligent
account routing for new signals based on tax efficiency.

Classes:
    TaxEngine: Core tax-aware engine with lot tracking, harvesting, and routing.

Models:
    TaxLot, HarvestCandidate, AccountRecommendation, TaxSummary, WashSaleCheck,
    TaxImpact, AccountSummary.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel

from db.database import Database

logger = logging.getLogger(__name__)

# Tax rate assumptions for estimation
SHORT_TERM_RATE = 0.37  # Ordinary income rate (top bracket)
LONG_TERM_RATE = 0.20  # Long-term capital gains rate
ONE_YEAR_DAYS = 365
WASH_SALE_WINDOW_DAYS = 30

# Similar-but-not-identical replacement mappings for tax-loss harvesting
_REPLACEMENT_MAP: dict[str, str] = {
    "NVDA": "AMD",
    "AMD": "NVDA",
    "MSFT": "GOOG",
    "GOOG": "MSFT",
    "AAPL": "MSFT",
    "TSLA": "RIVN",
    "RIVN": "TSLA",
    "AMZN": "SHOP",
    "SHOP": "AMZN",
    "META": "SNAP",
    "SNAP": "META",
    "AVGO": "MRVL",
    "MRVL": "AVGO",
    "CRM": "NOW",
    "NOW": "CRM",
}

# Account types that are taxable
_TAXABLE_TYPES = {"individual_brokerage"}


# ── Pydantic Models ──


class TaxLot(BaseModel):
    """A single tax lot with cost basis and gain/loss calculations."""

    id: int
    symbol: str
    shares: float
    cost_basis: float  # per share
    acquired_date: str
    account_id: int
    is_long_term: bool
    unrealized_gain: float
    unrealized_gain_pct: float


class HarvestCandidate(BaseModel):
    """A position with unrealized losses suitable for tax-loss harvesting."""

    symbol: str
    account_id: int
    unrealized_loss: float
    loss_pct: float
    shares: float
    cost_basis: float
    current_price: float
    wash_sale_risk: bool
    suggested_replacement: str | None


class AccountRecommendation(BaseModel):
    """Recommendation for which account to route a trade to."""

    account_id: int
    account_name: str
    account_type: str
    reasoning: str
    tax_savings_estimate: float | None


class TaxSummary(BaseModel):
    """Per-account tax summary with realized/unrealized gains breakdown."""

    account_id: int
    account_type: str
    realized_st_gains: float
    realized_lt_gains: float
    unrealized_st_gains: float
    unrealized_lt_gains: float
    estimated_tax_liability: float
    harvesting_opportunities: float


class WashSaleCheck(BaseModel):
    """Result of checking whether selling a symbol would trigger a wash sale."""

    symbol: str
    is_wash_sale: bool
    conflicting_buys: list[dict[str, Any]]
    watchlist_entries: list[dict[str, Any]]
    warning: str


class TaxImpact(BaseModel):
    """Estimated tax impact of selling shares in a given account."""

    symbol: str
    shares: float
    account_id: int
    account_type: str
    realized_gain: float
    is_long_term: bool
    estimated_tax: float
    effective_rate: float
    net_proceeds: float


class AccountSummary(BaseModel):
    """Summary of a single account's tax position."""

    account_id: int
    account_name: str
    account_type: str
    total_value: float
    total_cost_basis: float
    unrealized_gain: float
    unrealized_gain_pct: float
    tax_lots_count: int
    long_term_count: int
    short_term_count: int


# ── Engine ──


class TaxEngine:
    """Tax-aware engine for multi-account lot tracking, harvesting, and routing.

    Manages tax lots across multiple account types (taxable brokerage, Traditional
    IRA, Roth IRA) with FIFO/LIFO lot matching, wash sale detection across all
    accounts, tax-loss harvesting identification, and intelligent account routing.

    Attributes:
        db: Database instance.
        user_id: User ID for queries.
    """

    def __init__(self, db: Database, user_id: int = 1) -> None:
        self.db = db
        self.user_id = user_id

    # ── Tax Lot CRUD ──

    def create_tax_lot(
        self,
        symbol: str,
        shares: float,
        cost_per_share: float,
        acquired_date: str,
        account_id: int,
        lot_method: str = "fifo",
    ) -> int:
        """Create a new tax lot and return its ID."""
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO tax_lots
                   (symbol, shares, cost_per_share, acquired_date, account_id,
                    lot_method, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (symbol, shares, cost_per_share, acquired_date, account_id,
                 lot_method, self.user_id),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def sell_lots(
        self,
        symbol: str,
        shares_to_sell: float,
        sell_price: float,
        account_id: int,
        method: str = "fifo",
    ) -> list[dict[str, Any]]:
        """Sell shares using the specified lot method. Returns consumed lots info."""
        lots = self._get_open_lots(account_id=account_id, symbol=symbol)
        if method == "lifo":
            lots = sorted(lots, key=lambda r: r["acquired_date"], reverse=True)
        # FIFO is default (sorted by acquired_date ascending)

        remaining = shares_to_sell
        consumed: list[dict[str, Any]] = []
        sell_date = datetime.now().strftime("%Y-%m-%d")

        for lot in lots:
            if remaining <= 0:
                break
            take = min(remaining, lot["shares"])
            gain = (sell_price - lot["cost_per_share"]) * take
            is_loss = gain < 0

            # Check wash sale if selling at a loss
            is_wash = False
            if is_loss:
                ws = self.check_wash_sale(symbol, sell_date)
                is_wash = ws.is_wash_sale

            with self.db.transaction() as conn:
                if take >= lot["shares"]:
                    # Close entire lot
                    conn.execute(
                        """UPDATE tax_lots SET sold_date = ?, sold_price = ?,
                           is_wash_sale = ? WHERE id = ?""",
                        (sell_date, sell_price, int(is_wash), lot["id"]),
                    )
                else:
                    # Partial: reduce shares, create sold lot
                    conn.execute(
                        "UPDATE tax_lots SET shares = shares - ? WHERE id = ?",
                        (take, lot["id"]),
                    )
                    conn.execute(
                        """INSERT INTO tax_lots
                           (symbol, shares, cost_per_share, acquired_date, sold_date,
                            sold_price, account_id, lot_method, is_wash_sale, user_id)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (symbol, take, lot["cost_per_share"], lot["acquired_date"],
                         sell_date, sell_price, account_id, method, int(is_wash),
                         self.user_id),
                    )

                # Add to wash sale watchlist if loss
                if is_loss:
                    expiry = (datetime.strptime(sell_date, "%Y-%m-%d")
                              + timedelta(days=WASH_SALE_WINDOW_DAYS)).strftime("%Y-%m-%d")
                    conn.execute(
                        """INSERT INTO wash_sale_watchlist
                           (symbol, sell_date, sell_account_id, expiry_date,
                            shares, loss_amount, triggered)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (symbol, sell_date, account_id, expiry, take,
                         abs(gain), int(is_wash)),
                    )

            consumed.append({
                "lot_id": lot["id"],
                "shares": take,
                "cost_per_share": lot["cost_per_share"],
                "sell_price": sell_price,
                "gain": gain,
                "is_wash_sale": is_wash,
            })
            remaining -= take

        return consumed

    def get_tax_lots(
        self,
        account_id: int | None = None,
        symbol: str | None = None,
        current_prices: dict[str, float] | None = None,
    ) -> list[TaxLot]:
        """Get tax lots with unrealized gain calculations."""
        lots = self._get_open_lots(account_id=account_id, symbol=symbol)
        prices = current_prices or {}
        today = datetime.now()
        result: list[TaxLot] = []

        for lot in lots:
            price = prices.get(lot["symbol"], lot["cost_per_share"])
            gain = (price - lot["cost_per_share"]) * lot["shares"]
            gain_pct = ((price / lot["cost_per_share"]) - 1) * 100 if lot["cost_per_share"] else 0
            acq = datetime.strptime(lot["acquired_date"], "%Y-%m-%d")
            is_lt = (today - acq).days >= ONE_YEAR_DAYS

            result.append(TaxLot(
                id=lot["id"],
                symbol=lot["symbol"],
                shares=lot["shares"],
                cost_basis=lot["cost_per_share"],
                acquired_date=lot["acquired_date"],
                account_id=lot["account_id"],
                is_long_term=is_lt,
                unrealized_gain=round(gain, 2),
                unrealized_gain_pct=round(gain_pct, 2),
            ))
        return result

    def calculate_gains(
        self,
        account_id: int,
        current_prices: dict[str, float] | None = None,
    ) -> TaxSummary:
        """Calculate realized and unrealized gains for an account."""
        account = self.db.fetchone(
            "SELECT account_type FROM accounts WHERE id = ?", (account_id,)
        )
        account_type = account["account_type"] if account else "unknown"

        # Realized gains from sold lots
        sold = self.db.fetchall(
            """SELECT cost_per_share, sold_price, shares, acquired_date, sold_date
               FROM tax_lots WHERE account_id = ? AND sold_date IS NOT NULL
               AND is_wash_sale = 0""",
            (account_id,),
        )
        realized_st = 0.0
        realized_lt = 0.0
        for s in sold:
            gain = (s["sold_price"] - s["cost_per_share"]) * s["shares"]
            acq = datetime.strptime(s["acquired_date"], "%Y-%m-%d")
            sold_dt = datetime.strptime(s["sold_date"], "%Y-%m-%d")
            if (sold_dt - acq).days >= ONE_YEAR_DAYS:
                realized_lt += gain
            else:
                realized_st += gain

        # Unrealized from open lots
        open_lots = self.get_tax_lots(account_id=account_id, current_prices=current_prices)
        unrealized_st = sum(lot.unrealized_gain for lot in open_lots if not lot.is_long_term)
        unrealized_lt = sum(lot.unrealized_gain for lot in open_lots if lot.is_long_term)

        # Harvesting opportunities (unrealized losses in taxable accounts)
        harvest = 0.0
        if account_type in _TAXABLE_TYPES:
            harvest = sum(lot.unrealized_gain for lot in open_lots if lot.unrealized_gain < 0)

        # Tax liability (only for taxable accounts)
        tax = 0.0
        if account_type in _TAXABLE_TYPES:
            tax = max(0, realized_st * SHORT_TERM_RATE + realized_lt * LONG_TERM_RATE)

        return TaxSummary(
            account_id=account_id,
            account_type=account_type,
            realized_st_gains=round(realized_st, 2),
            realized_lt_gains=round(realized_lt, 2),
            unrealized_st_gains=round(unrealized_st, 2),
            unrealized_lt_gains=round(unrealized_lt, 2),
            estimated_tax_liability=round(tax, 2),
            harvesting_opportunities=round(abs(harvest), 2),
        )

    def find_harvest_candidates(
        self,
        min_loss: float = 500,
        min_loss_pct: float = 5.0,
        current_prices: dict[str, float] | None = None,
    ) -> list[HarvestCandidate]:
        """Find positions in taxable accounts with harvestable losses."""
        # Get taxable accounts
        accounts = self.db.fetchall(
            "SELECT id FROM accounts WHERE account_type IN ('individual_brokerage') "
            "AND user_id = ?",
            (self.user_id,),
        )
        if not accounts:
            return []

        candidates: list[HarvestCandidate] = []
        all_account_ids = [a["id"] for a in self.db.fetchall(
            "SELECT id FROM accounts WHERE user_id = ?", (self.user_id,)
        )]

        for acct in accounts:
            lots = self.get_tax_lots(
                account_id=acct["id"], current_prices=current_prices
            )
            # Group by symbol
            by_symbol: dict[str, list[TaxLot]] = {}
            for lot in lots:
                by_symbol.setdefault(lot.symbol, []).append(lot)

            for symbol, symbol_lots in by_symbol.items():
                total_loss = sum(
                    lot.unrealized_gain for lot in symbol_lots
                    if lot.unrealized_gain < 0
                )
                if total_loss >= 0:
                    continue
                total_loss = abs(total_loss)

                total_shares = sum(lot.shares for lot in symbol_lots)
                avg_cost = (sum(lot.cost_basis * lot.shares for lot in symbol_lots)
                            / total_shares if total_shares else 0)
                price = (current_prices or {}).get(symbol, avg_cost)
                loss_pct = ((avg_cost - price) / avg_cost * 100) if avg_cost else 0

                if total_loss < min_loss and loss_pct < min_loss_pct:
                    continue

                # Check wash sale risk: same symbol in other accounts
                other_holds = self.db.fetchall(
                    """SELECT account_id FROM tax_lots
                       WHERE symbol = ? AND sold_date IS NULL
                       AND account_id != ? AND account_id IN ({})""".format(
                        ",".join("?" * len(all_account_ids))
                    ),
                    (symbol, acct["id"], *all_account_ids),
                )
                wash_risk = len(other_holds) > 0

                candidates.append(HarvestCandidate(
                    symbol=symbol,
                    account_id=acct["id"],
                    unrealized_loss=round(-total_loss, 2),
                    loss_pct=round(-loss_pct, 2),
                    shares=total_shares,
                    cost_basis=round(avg_cost, 2),
                    current_price=round(price, 2),
                    wash_sale_risk=wash_risk,
                    suggested_replacement=_REPLACEMENT_MAP.get(symbol),
                ))

        return candidates

    def check_wash_sale(self, symbol: str, sell_date: str) -> WashSaleCheck:
        """Check if selling a symbol would trigger a wash sale across ALL accounts."""
        sell_dt = datetime.strptime(sell_date, "%Y-%m-%d")
        window_start = (sell_dt - timedelta(days=WASH_SALE_WINDOW_DAYS)).strftime("%Y-%m-%d")
        window_end = (sell_dt + timedelta(days=WASH_SALE_WINDOW_DAYS)).strftime("%Y-%m-%d")

        # Check for buys of same symbol across ALL accounts within 30-day window
        buys = self.db.fetchall(
            """SELECT id, symbol, shares, cost_per_share, acquired_date, account_id
               FROM tax_lots
               WHERE symbol = ? AND acquired_date BETWEEN ? AND ?
               AND sold_date IS NULL""",
            (symbol, window_start, window_end),
        )

        # Also check existing watchlist entries
        watchlist = self.db.fetchall(
            """SELECT * FROM wash_sale_watchlist
               WHERE symbol = ? AND expiry_date >= ?""",
            (symbol, sell_date),
        )

        is_wash = len(buys) > 0 or len(watchlist) > 0
        warning = ""
        if is_wash:
            acct_ids = {b["account_id"] for b in buys}
            warning = (f"Wash sale risk: {symbol} bought in account(s) "
                       f"{acct_ids} within 30-day window")

        return WashSaleCheck(
            symbol=symbol,
            is_wash_sale=is_wash,
            conflicting_buys=buys,
            watchlist_entries=watchlist,
            warning=warning,
        )

    def recommend_account(
        self,
        signal: Any,
        current_prices: dict[str, float] | None = None,
    ) -> AccountRecommendation:
        """Recommend which account to execute a trade in.

        Rules:
        - High-growth/high-turnover → Roth IRA (tax-free gains)
        - Dividend-heavy stocks (yield > 2%) → Traditional IRA (defer income tax)
        - Tax-loss harvesting candidates → taxable account
        - Short-term trades (horizon < 1 year) → Roth IRA (avoid short-term cap gains)
        - Long-term holds → Roth IRA preferred, then Traditional, then taxable
        - Consider available cash/buying power per account
        """
        accounts = self.db.fetchall(
            "SELECT id, name, account_type FROM accounts WHERE user_id = ? AND active = 1",
            (self.user_id,),
        )
        if not accounts:
            return AccountRecommendation(
                account_id=0, account_name="None", account_type="none",
                reasoning="No active accounts found", tax_savings_estimate=None,
            )

        acct_map = {a["account_type"]: a for a in accounts}
        roth = acct_map.get("roth_ira")
        trad = acct_map.get("traditional_ira")
        taxable = acct_map.get("individual_brokerage")

        # Extract signal properties
        horizon = getattr(signal, "horizon", "") or ""
        symbol = getattr(signal, "symbol", "")
        action = str(getattr(signal, "action", "BUY")).upper()

        # Tax-loss harvesting: must be in taxable account
        if action == "SELL":
            prices = current_prices or {}
            if taxable and symbol in prices:
                lots = self.get_tax_lots(
                    account_id=taxable["id"], symbol=symbol,
                    current_prices=prices,
                )
                has_losses = any(lot.unrealized_gain < 0 for lot in lots)
                if has_losses:
                    return AccountRecommendation(
                        account_id=taxable["id"],
                        account_name=taxable["name"],
                        account_type=taxable["account_type"],
                        reasoning="Tax-loss harvesting opportunity in taxable account",
                        tax_savings_estimate=None,
                    )

        # Short-term trades → Roth IRA
        short_horizons = {"1d", "1w", "2w", "1m", "2m", "3m", "6m"}
        if horizon.lower() in short_horizons and roth:
            est = self._estimate_savings(signal, "roth_ira")
            return AccountRecommendation(
                account_id=roth["id"],
                account_name=roth["name"],
                account_type=roth["account_type"],
                reasoning=f"Short-term trade ({horizon}) → Roth IRA avoids capital gains tax",
                tax_savings_estimate=est,
            )

        # Default priority: Roth > Traditional > Taxable
        if roth:
            est = self._estimate_savings(signal, "roth_ira")
            return AccountRecommendation(
                account_id=roth["id"],
                account_name=roth["name"],
                account_type=roth["account_type"],
                reasoning="Roth IRA preferred for tax-free growth",
                tax_savings_estimate=est,
            )
        if trad:
            return AccountRecommendation(
                account_id=trad["id"],
                account_name=trad["name"],
                account_type=trad["account_type"],
                reasoning="Traditional IRA defers tax on gains",
                tax_savings_estimate=None,
            )
        if taxable:
            return AccountRecommendation(
                account_id=taxable["id"],
                account_name=taxable["name"],
                account_type=taxable["account_type"],
                reasoning="Only available account (taxable)",
                tax_savings_estimate=None,
            )

        first = accounts[0]
        return AccountRecommendation(
            account_id=first["id"],
            account_name=first["name"],
            account_type=first["account_type"],
            reasoning="Default account selection",
            tax_savings_estimate=None,
        )

    def estimate_tax_impact(
        self,
        symbol: str,
        shares: float,
        account_id: int,
        current_price: float,
    ) -> TaxImpact:
        """Estimate the tax impact of selling shares in a specific account."""
        account = self.db.fetchone(
            "SELECT account_type FROM accounts WHERE id = ?", (account_id,)
        )
        account_type = account["account_type"] if account else "unknown"

        lots = self._get_open_lots(account_id=account_id, symbol=symbol)
        remaining = shares
        total_gain = 0.0
        total_cost = 0.0
        all_long_term = True
        today = datetime.now()

        for lot in lots:
            if remaining <= 0:
                break
            take = min(remaining, lot["shares"])
            gain = (current_price - lot["cost_per_share"]) * take
            total_gain += gain
            total_cost += lot["cost_per_share"] * take
            acq = datetime.strptime(lot["acquired_date"], "%Y-%m-%d")
            if (today - acq).days < ONE_YEAR_DAYS:
                all_long_term = False
            remaining -= take

        # IRA accounts: no tax on trades within the account
        if account_type in ("roth_ira", "traditional_ira"):
            tax = 0.0
            rate = 0.0
        else:
            rate = LONG_TERM_RATE if all_long_term else SHORT_TERM_RATE
            tax = max(0, total_gain * rate)

        proceeds = current_price * shares

        return TaxImpact(
            symbol=symbol,
            shares=shares,
            account_id=account_id,
            account_type=account_type,
            realized_gain=round(total_gain, 2),
            is_long_term=all_long_term,
            estimated_tax=round(tax, 2),
            effective_rate=round(rate, 4),
            net_proceeds=round(proceeds - tax, 2),
        )

    def get_account_summary(
        self,
        current_prices: dict[str, float] | None = None,
    ) -> list[AccountSummary]:
        """Get per-account tax position summary."""
        accounts = self.db.fetchall(
            "SELECT id, name, account_type FROM accounts WHERE user_id = ? AND active = 1",
            (self.user_id,),
        )
        result: list[AccountSummary] = []
        prices = current_prices or {}

        for acct in accounts:
            lots = self.get_tax_lots(account_id=acct["id"], current_prices=prices)
            total_value = sum(
                (prices.get(lot.symbol, lot.cost_basis)) * lot.shares for lot in lots
            )
            total_cost = sum(lot.cost_basis * lot.shares for lot in lots)
            unrealized = total_value - total_cost
            gain_pct = (unrealized / total_cost * 100) if total_cost else 0
            lt_count = sum(1 for lot in lots if lot.is_long_term)
            st_count = sum(1 for lot in lots if not lot.is_long_term)

            result.append(AccountSummary(
                account_id=acct["id"],
                account_name=acct["name"],
                account_type=acct["account_type"],
                total_value=round(total_value, 2),
                total_cost_basis=round(total_cost, 2),
                unrealized_gain=round(unrealized, 2),
                unrealized_gain_pct=round(gain_pct, 2),
                tax_lots_count=len(lots),
                long_term_count=lt_count,
                short_term_count=st_count,
            ))

        return result

    # ── Private helpers ──

    def _get_open_lots(
        self,
        account_id: int | None = None,
        symbol: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get open (unsold) tax lots, optionally filtered."""
        sql = "SELECT * FROM tax_lots WHERE sold_date IS NULL"
        params: list[Any] = []
        if account_id is not None:
            sql += " AND account_id = ?"
            params.append(account_id)
        if symbol is not None:
            sql += " AND symbol = ?"
            params.append(symbol)
        sql += " ORDER BY acquired_date ASC"
        return self.db.fetchall(sql, tuple(params))

    def _estimate_savings(self, signal: Any, target_type: str) -> float | None:
        """Rough estimate of tax savings from routing to a tax-advantaged account."""
        size_pct = getattr(signal, "size_pct", None)
        if not size_pct:
            return None
        # Assume $100k portfolio, estimate based on position size
        estimated_position = 100_000 * size_pct
        # Assume 20% gain, short-term
        estimated_gain = estimated_position * 0.20
        savings = estimated_gain * SHORT_TERM_RATE
        return round(savings, 2)
