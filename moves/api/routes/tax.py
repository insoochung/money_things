"""Tax API routes for tax lot tracking, harvesting, and account recommendations.

Endpoints:
    GET /tax/summary — per-account tax summary
    GET /tax/lots — tax lot detail (filterable by account_id, symbol)
    GET /tax/harvest-candidates — harvesting opportunities
    GET /tax/wash-sale-check/{symbol} — check wash sale risk
    GET /tax/account-recommendation/{signal_id} — which account for a signal
    GET /tax/impact/{symbol} — tax impact of selling
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import EngineContainer, get_engines
from engine.tax_engine import (
    AccountRecommendation,
    AccountSummary,
    HarvestCandidate,
    TaxImpact,
    TaxLot,
    TaxSummary,
    WashSaleCheck,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tax", tags=["tax"])


def _get_tax_engine(engines: EngineContainer = Depends(get_engines)):  # noqa: B008
    """Create a TaxEngine from the shared database."""
    from engine.tax_engine import TaxEngine
    return TaxEngine(db=engines.db)


def _get_prices(engines: EngineContainer) -> dict[str, float]:
    """Fetch current prices for all positioned symbols."""
    positions = engines.db.fetchall(
        "SELECT DISTINCT symbol FROM tax_lots WHERE sold_date IS NULL"
    )
    prices: dict[str, float] = {}
    for p in positions:
        try:
            price = engines.pricing.get_price(p["symbol"])
            if price:
                prices[p["symbol"]] = price
        except Exception:
            pass
    return prices


@router.get("/summary", response_model=list[TaxSummary])
async def tax_summary(engines: EngineContainer = Depends(get_engines)):  # noqa: B008
    """Per-account tax summary with realized/unrealized gains."""
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    accounts = engines.db.fetchall(
        "SELECT id FROM accounts WHERE active = 1"
    )
    return [tax.calculate_gains(a["id"], current_prices=prices) for a in accounts]


@router.get("/lots", response_model=list[TaxLot])
async def tax_lots(
    account_id: int | None = Query(None),
    symbol: str | None = Query(None),
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Tax lot detail, filterable by account and symbol."""
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    return tax.get_tax_lots(account_id=account_id, symbol=symbol, current_prices=prices)


@router.get("/harvest-candidates", response_model=list[HarvestCandidate])
async def harvest_candidates(
    min_loss: float = Query(500),
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Find tax-loss harvesting opportunities in taxable accounts."""
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    return tax.find_harvest_candidates(min_loss=min_loss, current_prices=prices)


@router.get("/wash-sale-check/{symbol}", response_model=WashSaleCheck)
async def wash_sale_check(
    symbol: str,
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Check wash sale risk for a symbol."""
    from datetime import datetime
    tax = _get_tax_engine(engines)
    today = datetime.now().strftime("%Y-%m-%d")
    return tax.check_wash_sale(symbol, today)


@router.get("/account-recommendation/{signal_id}", response_model=AccountRecommendation)
async def account_recommendation(
    signal_id: int,
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Recommend which account to route a signal to."""
    signal = engines.db.fetchone("SELECT * FROM signals WHERE id = ?", (signal_id,))
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    from engine import Signal, SignalAction, SignalSource, SignalStatus
    sig = Signal(
        id=signal["id"],
        action=SignalAction(signal["action"]),
        symbol=signal["symbol"],
        thesis_id=signal.get("thesis_id"),
        confidence=signal.get("confidence", 0.5),
        source=SignalSource(signal.get("source", "manual")),
        horizon=signal.get("horizon", ""),
        status=SignalStatus(signal.get("status", "pending")),
        size_pct=signal.get("size_pct"),
    )
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    return tax.recommend_account(sig, current_prices=prices)


@router.get("/impact/{symbol}", response_model=TaxImpact)
async def tax_impact(
    symbol: str,
    shares: float = Query(...),
    account_id: int = Query(...),
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Estimate tax impact of selling shares."""
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    price = prices.get(symbol)
    if not price:
        try:
            price = engines.pricing.get_price(symbol)
        except Exception:
            raise HTTPException(status_code=404, detail=f"Cannot get price for {symbol}")
    if not price:
        raise HTTPException(status_code=404, detail=f"Cannot get price for {symbol}")
    return tax.estimate_tax_impact(symbol, shares, account_id, price)


@router.get("/accounts", response_model=list[AccountSummary])
async def account_summaries(
    engines: EngineContainer = Depends(get_engines),  # noqa: B008
):
    """Per-account summary of tax positions."""
    tax = _get_tax_engine(engines)
    prices = _get_prices(engines)
    return tax.get_account_summary(current_prices=prices)
