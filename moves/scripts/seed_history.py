"""Seed 1 year of realistic NAV history using actual META/QCOM prices.

Portfolio: META 230sh (Schwab), QCOM 129sh (E*Trade), ~$1,136 cash.
Fetches real daily closes from yfinance and computes daily NAV.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from db.database import Database


def main(db_path: str = "data/moves_mock.db") -> None:
    db = Database(db_path)

    # Portfolio composition
    holdings = {"META": 230, "QCOM": 129}
    cash = 1136.0

    # Fetch 1 year of daily data
    tickers = yf.Tickers(" ".join(holdings.keys()))
    hist = tickers.history(period="1y", interval="1d")

    if hist.empty:
        print("Failed to fetch price data")
        return

    closes = hist["Close"]
    dates = closes.index

    # Clear existing NAV data
    db.execute("DELETE FROM portfolio_value")
    db.connect().commit()

    # Also seed positions if missing
    pos_count = db.fetchone("SELECT COUNT(*) as c FROM positions")
    if not pos_count or pos_count["c"] == 0:
        _seed_positions(db, holdings)

    rows = 0
    prev_nav = None
    for dt in dates:
        date_str = dt.strftime("%Y-%m-%d")
        nav = cash

        skip = False
        for sym, shares in holdings.items():
            price = closes[sym].loc[dt]
            if price != price:  # NaN check
                skip = True
                break
            nav += price * shares

        if skip:
            continue

        daily_return = 0.0
        if prev_nav and prev_nav > 0:
            daily_return = ((nav - prev_nav) / prev_nav) * 100

        # Compute long value (stock value only)
        long_value = nav - cash

        db.execute(
            """INSERT OR REPLACE INTO portfolio_value
               (date, total_value, long_value, short_value, cash,
                cost_basis, daily_return_pct)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (date_str, round(nav, 2), round(long_value, 2), cash,
             round(long_value * 0.85, 2), round(daily_return, 4)),
        )
        prev_nav = nav
        rows += 1

    db.connect().commit()
    print(f"✓ Seeded {rows} days of NAV history")

    # Show summary
    first = db.fetchone(
        "SELECT date, total_value FROM portfolio_value ORDER BY date LIMIT 1",
    )
    last = db.fetchone(
        "SELECT date, total_value FROM portfolio_value ORDER BY date DESC LIMIT 1",
    )
    if first and last:
        ret = ((last["total_value"] - first["total_value"])
               / first["total_value"] * 100)
        print(f"  {first['date']}: ${first['total_value']:,.0f}")
        print(f"  {last['date']}: ${last['total_value']:,.0f}")
        print(f"  Return: {ret:+.1f}%")


def _seed_positions(db: Database, holdings: dict[str, int]) -> None:
    """Seed positions for META and QCOM."""
    acct_map = {"META": 1, "QCOM": 2}  # Schwab=1, E*Trade=2
    avg_costs = {"META": 485.0, "QCOM": 155.0}

    for sym, shares in holdings.items():
        db.execute(
            """INSERT INTO positions
               (account_id, symbol, shares, avg_cost, side, strategy)
               VALUES (?, ?, ?, ?, 'long', 'RSU hold')""",
            (acct_map.get(sym, 1), sym, shares, avg_costs.get(sym, 0)),
        )
    db.connect().commit()
    print("  ✓ Seeded positions")


if __name__ == "__main__":
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/moves_mock.db"
    main(db_path)
