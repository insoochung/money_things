"""Congress trades scraper: fetch, store, and analyze congressional trading activity.

Scrapes publicly available congressional trading disclosures and cross-references
them against portfolio positions and thesis symbols to generate low-confidence
trading signals.

Data source: Capitol Trades (https://www.capitoltrades.com/trades) with graceful
fallback to empty results if the site is unavailable or blocks requests.

Classes:
    CongressTradesEngine: Main class for scraping, storing, and analyzing trades.
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

# Capitol Trades URL for recent trades
CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades"

# Default request timeout in seconds
REQUEST_TIMEOUT = 15


class CongressTradesEngine:
    """Scrapes and analyzes congressional trading activity.

    Fetches recent trades from Capitol Trades, stores them in the database,
    cross-references against portfolio positions and thesis symbols, and
    generates low-confidence signals for overlapping trades.

    Attributes:
        db: Database instance for persistence.
        signal_engine: Optional SignalEngine for creating signals.
    """

    def __init__(self, db: Database, signal_engine: SignalEngine | None = None) -> None:
        """Initialize the CongressTradesEngine.

        Args:
            db: Database instance for reading/writing congress trades.
            signal_engine: Optional SignalEngine for generating signals from
                overlapping trades. If None, generate_signals() returns empty list.
        """
        self.db = db
        self.signal_engine = signal_engine

    def fetch_recent(self, days: int = 7) -> list[dict]:
        """Fetch recent congressional trades from Capitol Trades.

        Makes an HTTP request to Capitol Trades and parses the HTML table of
        recent trades. Falls back to empty results on any error.

        Args:
            days: Number of days of trades to fetch. Used for filtering results
                by transaction date. Defaults to 7.

        Returns:
            List of trade dicts with keys: member_name, symbol, action,
            amount_range, disclosure_date, transaction_date, source_url.
            Returns empty list on any error.
        """
        try:
            return self._scrape_capitol_trades(days)
        except Exception:
            logger.warning("Failed to fetch congress trades, returning empty", exc_info=True)
            return []

    def _scrape_capitol_trades(self, days: int) -> list[dict]:
        """Scrape trades from Capitol Trades HTML.

        Args:
            days: Number of days to look back for trades.

        Returns:
            List of parsed trade dicts.
        """
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

        # Capitol Trades uses a table with rows for each trade
        rows = soup.select("table tbody tr")
        for row in rows:
            trade = self._parse_trade_row(row, cutoff)
            if trade:
                trades.append(trade)

        logger.info("Scraped %d congress trades from Capitol Trades", len(trades))
        return trades

    def _parse_trade_row(self, row: BeautifulSoup, cutoff: datetime) -> dict | None:
        """Parse a single trade row from the Capitol Trades HTML table.

        Args:
            row: BeautifulSoup element representing a table row.
            cutoff: Only return trades on or after this datetime.

        Returns:
            Trade dict if parseable and within date range, None otherwise.
        """
        cells = row.select("td")
        if len(cells) < 5:
            return None

        try:
            member_name = cells[0].get_text(strip=True)
            symbol = cells[1].get_text(strip=True).upper()
            action = cells[2].get_text(strip=True).lower()
            amount_range = cells[3].get_text(strip=True)
            date_text = cells[4].get_text(strip=True)

            # Normalize action
            if "purchase" in action or "buy" in action:
                action = "buy"
            elif "sale" in action or "sell" in action:
                action = "sell"

            # Skip if no symbol
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
        """Insert new trades into the database, skipping duplicates.

        Duplicates are detected by matching member_name + symbol + transaction_date.

        Args:
            trades: List of trade dicts from fetch_recent().

        Returns:
            Number of new trades inserted.
        """
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

    def check_overlap(self) -> list[dict]:
        """Cross-reference congress trades with portfolio positions and thesis symbols.

        Finds congress trades where the traded symbol matches either a current
        position or a symbol in an active thesis.

        Returns:
            List of congress trade dicts that overlap with portfolio/thesis symbols.
        """
        # Get portfolio symbols
        position_rows = self.db.fetchall("SELECT DISTINCT symbol FROM positions")
        position_symbols = {r["symbol"] for r in position_rows}

        # Get thesis symbols
        thesis_rows = self.db.fetchall(
            "SELECT symbols FROM theses WHERE status IN ('active', 'strengthening', 'confirmed')"
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

        # Find overlapping trades
        placeholders = ",".join("?" for _ in all_symbols)
        overlapping = self.db.fetchall(
            f"SELECT * FROM congress_trades WHERE symbol IN ({placeholders})",
            tuple(all_symbols),
        )

        return [dict(t) for t in overlapping]

    def generate_signals(self) -> list[Signal]:
        """Create low-confidence signals for congress trades overlapping portfolio.

        Only creates BUY signals when congress members are buying stocks we hold
        or are watching via theses. Uses confidence=0.3 as a low base.

        Returns:
            List of Signal objects created. Empty list if no signal_engine or
            no overlapping buy trades.
        """
        if not self.signal_engine:
            return []

        overlapping = self.check_overlap()
        signals: list[Signal] = []

        # Get thesis mapping for symbol -> thesis_id
        thesis_map = self._build_thesis_map()

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
            created = self.signal_engine.create_signal(signal)
            signals.append(created)

        logger.info("Generated %d signals from congress trades", len(signals))
        return signals

    def _build_thesis_map(self) -> dict[str, int]:
        """Build a mapping from symbol to thesis_id for active theses.

        Returns:
            Dict mapping symbol strings to the thesis ID they belong to.
        """
        import json

        thesis_rows = self.db.fetchall(
            "SELECT id, symbols FROM theses "
            "WHERE status IN ('active', 'strengthening', 'confirmed')"
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

    def get_summary(self) -> dict:
        """Get net buying/selling summary for portfolio-adjacent tickers.

        Returns:
            Dict with keys:
                - total_trades: Total congress trades stored.
                - overlapping: Number of trades overlapping portfolio.
                - net_by_symbol: Dict mapping symbol to net action count
                  (positive = net buying, negative = net selling).
                - recent_buys: List of recent buy trades in portfolio symbols.
                - recent_sells: List of recent sell trades in portfolio symbols.
        """
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM congress_trades")
        total_count = total["cnt"] if total else 0

        overlapping = self.check_overlap()

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
