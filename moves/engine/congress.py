"""Congress trades scraper: fetch, store, and analyze congressional trading activity.

Scraping stays global (no user_id). Overlap detection uses user's positions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus

if TYPE_CHECKING:
    from engine.signals import SignalEngine

logger = logging.getLogger(__name__)

CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades"
REQUEST_TIMEOUT = 15


class CongressTradesEngine:
    """Scrapes and analyzes congressional trading activity."""

    def __init__(self, db: Database, signal_engine: SignalEngine | None = None) -> None:
        self.db = db
        self.signal_engine = signal_engine

    def fetch_recent(self, days: int = 7) -> list[dict]:
        """Fetch recent congressional trades from Capitol Trades."""
        try:
            return self._scrape_capitol_trades(days)
        except Exception:
            logger.warning("Failed to fetch congress trades, returning empty", exc_info=True)
            return []

    def _scrape_capitol_trades(self, days: int) -> list[dict]:
        """Scrape trades from Capitol Trades HTML."""
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MoneyMoves/1.0)",
            "Accept": "text/html",
        }
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(CAPITOL_TRADES_URL, headers=headers)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        cutoff = datetime.now(UTC) - timedelta(days=days)
        trades: list[dict] = []

        rows = soup.select("table tbody tr")
        for row in rows:
            trade = self._parse_trade_row(row, cutoff)
            if trade:
                trades.append(trade)

        logger.info("Scraped %d congress trades from Capitol Trades", len(trades))
        return trades

    def _parse_trade_row(self, row: BeautifulSoup, cutoff: datetime) -> dict | None:
        """Parse a single trade row from the Capitol Trades HTML table."""
        cells = row.select("td")
        if len(cells) < 5:
            return None

        try:
            member_name = cells[0].get_text(strip=True)
            symbol = cells[1].get_text(strip=True).upper()
            action = cells[2].get_text(strip=True).lower()
            amount_range = cells[3].get_text(strip=True)
            date_text = cells[4].get_text(strip=True)

            if "purchase" in action or "buy" in action:
                action = "buy"
            elif "sale" in action or "sell" in action:
                action = "sell"

            if not symbol or not symbol.isalpha():
                return None

            return {
                "politician": member_name,
                "symbol": symbol,
                "action": action,
                "amount_range": amount_range,
                "date_filed": date_text,
                "date_traded": date_text,
                "source_url": CAPITOL_TRADES_URL,
            }
        except (IndexError, AttributeError):
            return None

    def store_trades(self, trades: list[dict]) -> int:
        """Insert new trades into the database, skipping duplicates. Global (no user_id)."""
        inserted = 0
        for trade in trades:
            politician = trade.get("politician") or trade.get("member_name", "")
            symbol = trade["symbol"]
            date_traded = trade.get("date_traded") or trade.get("transaction_date", "")
            date_filed = trade.get("date_filed") or trade.get("disclosure_date", "")

            existing = self.db.fetchone(
                """SELECT id FROM congress_trades
                   WHERE politician = ? AND symbol = ? AND date_traded = ?""",
                (politician, symbol, date_traded),
            )
            if existing:
                continue

            self.db.execute(
                """INSERT INTO congress_trades
                   (politician, symbol, action, amount_range, date_filed,
                    date_traded, source_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    politician,
                    symbol,
                    trade["action"],
                    trade.get("amount_range", ""),
                    date_filed,
                    date_traded,
                    trade.get("source_url", ""),
                ),
            )
            inserted += 1

        if inserted:
            self.db.connect().commit()
        logger.info("Stored %d new congress trades (of %d fetched)", inserted, len(trades))
        return inserted

    def check_overlap(self, user_id: int) -> list[dict]:
        """Cross-reference congress trades with a user's positions and thesis symbols.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of congress trade dicts that overlap with the user's portfolio/thesis symbols.
        """
        position_rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM positions WHERE user_id = ?",
            (user_id,),
        )
        position_symbols = {r["symbol"] for r in position_rows}

        thesis_rows = self.db.fetchall(
            "SELECT symbols FROM theses WHERE status IN ('active', 'strengthening', 'confirmed') AND user_id = ?",
            (user_id,),
        )
        thesis_symbols: set[str] = set()
        for r in thesis_rows:
            import json

            try:
                syms = json.loads(r["symbols"]) if r["symbols"] else []
                thesis_symbols.update(syms)
            except (json.JSONDecodeError, TypeError):
                continue

        all_symbols = position_symbols | thesis_symbols

        if not all_symbols:
            return []

        placeholders = ",".join("?" for _ in all_symbols)
        overlapping = self.db.fetchall(
            f"SELECT * FROM congress_trades WHERE symbol IN ({placeholders})",
            tuple(all_symbols),
        )

        return [dict(t) for t in overlapping]

    def generate_signals(self, user_id: int) -> list[Signal]:
        """Create low-confidence signals for congress trades overlapping user's portfolio.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of Signal objects created.
        """
        if not self.signal_engine:
            return []

        overlapping = self.check_overlap(user_id)
        signals: list[Signal] = []

        thesis_map = self._build_thesis_map(user_id)

        for trade in overlapping:
            if trade.get("action") != "buy":
                continue

            symbol = trade["symbol"]
            thesis_id = thesis_map.get(symbol)

            signal = Signal(
                action=SignalAction.BUY,
                symbol=symbol,
                thesis_id=thesis_id,
                confidence=0.3,
                source=SignalSource.CONGRESS_TRADE,
                reasoning=f"Congress member {trade.get('politician', 'unknown')} bought {symbol}",
                status=SignalStatus.PENDING,
            )
            created = self.signal_engine.create_signal(signal, user_id)
            signals.append(created)

        logger.info("Generated %d signals from congress trades", len(signals))
        return signals

    def _build_thesis_map(self, user_id: int) -> dict[str, int]:
        """Build a mapping from symbol to thesis_id for a user's active theses.

        Args:
            user_id: ID of the owning user.

        Returns:
            Dict mapping symbol strings to the thesis ID.
        """
        import json

        thesis_rows = self.db.fetchall(
            "SELECT id, symbols FROM theses "
            "WHERE status IN ('active', 'strengthening', 'confirmed') AND user_id = ?",
            (user_id,),
        )
        mapping: dict[str, int] = {}
        for row in thesis_rows:
            try:
                syms = json.loads(row["symbols"]) if row["symbols"] else []
                for s in syms:
                    mapping[s] = row["id"]
            except (json.JSONDecodeError, TypeError):
                continue
        return mapping

    def get_summary(self, user_id: int) -> dict:
        """Get net buying/selling summary for a user's portfolio-adjacent tickers.

        Args:
            user_id: ID of the owning user.

        Returns:
            Dict with total_trades, overlapping, net_by_symbol, recent_buys, recent_sells.
        """
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM congress_trades")
        total_count = total["cnt"] if total else 0

        overlapping = self.check_overlap(user_id)

        net_by_symbol: dict[str, int] = {}
        recent_buys: list[dict] = []
        recent_sells: list[dict] = []

        for trade in overlapping:
            symbol = trade["symbol"]
            if trade["action"] == "buy":
                net_by_symbol[symbol] = net_by_symbol.get(symbol, 0) + 1
                recent_buys.append(dict(trade))
            elif trade["action"] == "sell":
                net_by_symbol[symbol] = net_by_symbol.get(symbol, 0) - 1
                recent_sells.append(dict(trade))

        return {
            "total_trades": total_count,
            "overlapping": len(overlapping),
            "net_by_symbol": net_by_symbol,
            "recent_buys": recent_buys,
            "recent_sells": recent_sells,
        }
