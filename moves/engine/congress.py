"""Congress trades scraper: fetch, store, and analyze congressional trading activity.

Scraping stays global (no user_id). Overlap detection uses user's positions.

Data sources (tried in order):
1. House Stock Watcher S3 dataset (free, no API key)
2. Capitol Trades HTML scraping (fallback)
3. Mock data for development (if both fail)
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus
from engine.congress_scoring import PoliticianScorer, calculate_disclosure_lag, parse_amount_bucket

if TYPE_CHECKING:
    from engine.signals import SignalEngine

logger = logging.getLogger(__name__)

CAPITOL_TRADES_URL = "https://www.capitoltrades.com/trades"
HOUSE_STOCK_WATCHER_URL = (
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json"
)
REQUEST_TIMEOUT = 30
RATE_LIMIT_DELAY = 1.0  # seconds between requests


class CongressTradesEngine:
    """Scrapes and analyzes congressional trading activity."""

    def __init__(self, db: Database, signal_engine: SignalEngine | None = None) -> None:
        self.db = db
        self.signal_engine = signal_engine
        self.scorer = PoliticianScorer(db)

    def fetch_recent(self, days: int = 7) -> list[dict]:
        """Fetch recent congressional trades from available sources.

        Tries House Stock Watcher S3 first, falls back to Capitol Trades HTML.

        Args:
            days: How many days back to look for trades.

        Returns:
            List of trade dicts with politician, symbol, action, amount_range,
            date_filed, date_traded, source_url keys.
        """
        # Try House Stock Watcher S3 dataset first (most reliable free source)
        try:
            trades = self._fetch_house_stock_watcher(days)
            if trades:
                return trades
        except Exception:
            logger.warning("House Stock Watcher fetch failed", exc_info=True)

        # Fallback to Capitol Trades HTML scraping
        try:
            time.sleep(RATE_LIMIT_DELAY)
            return self._scrape_capitol_trades(days)
        except Exception:
            logger.warning("Capitol Trades scrape failed", exc_info=True)

        logger.warning("All congress trade sources failed, returning empty")
        return []

    def _fetch_house_stock_watcher(self, days: int) -> list[dict]:
        """Fetch trades from the House Stock Watcher S3 dataset.

        Args:
            days: Only return trades from the last N days.

        Returns:
            List of parsed trade dicts.
        """
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MoneyMoves/1.0)"}
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(HOUSE_STOCK_WATCHER_URL, headers=headers)
            resp.raise_for_status()

        raw_trades = resp.json()
        cutoff = datetime.now(UTC) - timedelta(days=days)
        trades: list[dict] = []

        for raw in raw_trades:
            trade = self._parse_house_stock_watcher_entry(raw, cutoff)
            if trade:
                trades.append(trade)

        logger.info(
            "Fetched %d congress trades from House Stock Watcher (last %d days)",
            len(trades), days,
        )
        return trades

    def _parse_house_stock_watcher_entry(self, raw: dict, cutoff: datetime) -> dict | None:
        """Parse a single entry from the House Stock Watcher JSON dataset.

        Args:
            raw: Raw JSON dict from the S3 dataset.
            cutoff: Only return trades after this datetime.

        Returns:
            Parsed trade dict or None if invalid/too old.
        """
        try:
            # The dataset uses 'transaction_date' and 'disclosure_date'
            date_traded = raw.get("transaction_date", "")
            if not date_traded or date_traded == "--":
                return None

            # Parse date — format is typically "2024-01-15" or "01/15/2024"
            trade_dt = self._parse_date(date_traded)
            if trade_dt and trade_dt < cutoff:
                return None

            ticker = raw.get("ticker", "").strip().upper()
            if not ticker or ticker == "--" or not ticker.isalpha() or len(ticker) > 6:
                return None

            action_raw = raw.get("type", "").lower()
            if "purchase" in action_raw or "buy" in action_raw:
                action = "buy"
            elif "sale" in action_raw or "sell" in action_raw:
                action = "sell"
            elif "exchange" in action_raw:
                action = "exchange"
            else:
                action = action_raw

            return {
                "politician": raw.get("representative", "Unknown"),
                "symbol": ticker,
                "action": action,
                "amount_range": raw.get("amount", ""),
                "date_filed": raw.get("disclosure_date", ""),
                "date_traded": date_traded,
                "source_url": raw.get("ptr_link", HOUSE_STOCK_WATCHER_URL),
            }
        except (KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        """Parse a date string in common formats.

        Args:
            date_str: Date string like '2024-01-15' or '01/15/2024'.

        Returns:
            Datetime object or None if unparseable.
        """
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(date_str, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None

    def _scrape_capitol_trades(self, days: int) -> list[dict]:
        """Scrape trades from Capitol Trades HTML.

        Args:
            days: Only return trades from the last N days.

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
            row: BeautifulSoup element for a table row.
            cutoff: Only return trades after this datetime.

        Returns:
            Parsed trade dict or None if invalid.
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

            enriched = self.scorer.enrich_trade(trade)
            lag = calculate_disclosure_lag(date_traded, date_filed)
            bucket = parse_amount_bucket(trade.get("amount_range", ""))
            self.db.execute(
                """INSERT INTO congress_trades
                   (politician, symbol, action, amount_range, date_filed,
                    date_traded, source_url, politician_score,
                    disclosure_lag_days, trade_size_bucket, committee_relevant)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    politician,
                    symbol,
                    trade["action"],
                    trade.get("amount_range", ""),
                    date_filed,
                    date_traded,
                    trade.get("source_url", ""),
                    enriched.get("politician_score"),
                    lag,
                    bucket,
                    enriched.get("committee_relevant", 0),
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
            "SELECT symbols FROM theses WHERE status IN "
            "('active', 'strengthening', 'confirmed') AND user_id = ?",
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

            # Enrich trade and check tier
            enriched = self.scorer.enrich_trade(trade)
            tier = enriched.get("politician_tier", "unknown")

            # Skip noise-tier politicians
            if tier == "noise":
                continue

            # Set confidence based on tier
            confidence_map = {"whale": 0.6, "notable": 0.45, "average": 0.3, "unknown": 0.3}
            confidence = confidence_map.get(tier, 0.3)

            reasoning = self.scorer.build_reasoning(enriched)

            signal = Signal(
                action=SignalAction.BUY,
                symbol=symbol,
                thesis_id=thesis_id,
                confidence=confidence,
                source=SignalSource.CONGRESS_TRADE,
                reasoning=reasoning,
                status=SignalStatus.PENDING,
            )
            created = self.signal_engine.create_signal(signal)
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
