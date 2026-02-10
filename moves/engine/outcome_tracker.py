"""Outcome tracker — measures thesis performance against actual returns.

Closes the feedback loop by comparing thesis predictions (conviction,
status, symbols) against real price performance. Produces scorecards
that help calibrate future conviction levels.

Usage:
    tracker = OutcomeTracker(db)
    scorecard = tracker.score_thesis(thesis_id)
    all_scores = tracker.score_all()
    tracker.persist_snapshot()  # saves to outcome_snapshots table
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from db.database import Database
from engine.pricing import get_history, get_price

logger = logging.getLogger(__name__)

# Migration SQL for outcome_snapshots table
MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS outcome_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    symbols TEXT NOT NULL,
    conviction REAL NOT NULL,
    avg_return_pct REAL,
    best_symbol TEXT,
    best_return_pct REAL,
    worst_symbol TEXT,
    worst_return_pct REAL,
    thesis_age_days INTEGER,
    calibration_score REAL,
    details_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(thesis_id, snapshot_date)
);
"""


@dataclass
class SymbolReturn:
    """Return data for a single symbol."""
    symbol: str
    current_price: float | None = None
    price_at_thesis_creation: float | None = None
    return_pct: float | None = None
    period_days: int = 0
    error: str | None = None


@dataclass
class ThesisScorecard:
    """Performance scorecard for a single thesis."""
    thesis_id: int
    title: str
    conviction: float
    status: str
    symbols: list[str]
    created_at: str
    age_days: int = 0
    symbol_returns: list[SymbolReturn] = field(default_factory=list)
    avg_return_pct: float | None = None
    best_symbol: str | None = None
    best_return_pct: float | None = None
    worst_symbol: str | None = None
    worst_return_pct: float | None = None
    calibration_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "thesis_id": self.thesis_id,
            "title": self.title,
            "conviction": self.conviction,
            "status": self.status,
            "symbols": self.symbols,
            "created_at": self.created_at,
            "age_days": self.age_days,
            "avg_return_pct": self.avg_return_pct,
            "best_symbol": self.best_symbol,
            "best_return_pct": self.best_return_pct,
            "worst_symbol": self.worst_symbol,
            "worst_return_pct": self.worst_return_pct,
            "calibration_score": self.calibration_score,
            "symbol_returns": [
                {
                    "symbol": sr.symbol,
                    "current_price": sr.current_price,
                    "return_pct": sr.return_pct,
                    "period_days": sr.period_days,
                    "error": sr.error,
                }
                for sr in self.symbol_returns
            ],
        }

    def format_telegram(self) -> str:
        """Format scorecard for Telegram display."""
        lines = [f"📊 **{self.title}**"]
        lines.append(
            f"Conviction: {int(self.conviction)}% | Age: {self.age_days}d | Status: {self.status}"
        )
        lines.append("")

        for sr in self.symbol_returns:
            if sr.error:
                lines.append(f"  ❓ {sr.symbol}: {sr.error}")
            elif sr.return_pct is not None:
                emoji = "📈" if sr.return_pct >= 0 else "📉"
                lines.append(f"  {emoji} {sr.symbol}: {sr.return_pct:+.1f}% ({sr.period_days}d)")

        if self.avg_return_pct is not None:
            emoji = "✅" if self.avg_return_pct >= 0 else "❌"
            lines.append(f"\n{emoji} Avg return: {self.avg_return_pct:+.1f}%")

        if self.calibration_score is not None:
            lines.append(f"🎯 Calibration: {self.calibration_score:.0f}/100")

        return "\n".join(lines)


class OutcomeTracker:
    """Tracks thesis outcomes against actual market returns.

    Args:
        db: Database instance for moves DB access.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create outcome_snapshots table if it doesn't exist."""
        try:
            self.db.execute(MIGRATION_SQL)
            self.db.connect().commit()
        except Exception as e:
            logger.warning("Could not create outcome_snapshots table: %s", e)

    def _parse_symbols(self, raw: str | list | None) -> list[str]:
        """Parse symbols from thesis row."""
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        try:
            parsed = json.loads(raw)
            return list(parsed) if isinstance(parsed, list) else [str(parsed)]
        except (json.JSONDecodeError, TypeError):
            return [s.strip() for s in raw.split(",") if s.strip()]

    def _get_symbol_return(self, symbol: str, since_date: str) -> SymbolReturn:
        """Calculate return for a symbol since a given date.

        Uses price_history table first (if available), falls back to
        yfinance history API.
        """
        sr = SymbolReturn(symbol=symbol)

        try:
            # Parse the since_date
            since_dt = datetime.fromisoformat(since_date.replace("Z", "+00:00"))
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            sr.period_days = (now - since_dt).days

            # Try to get historical price from DB first
            row = self.db.execute(
                """SELECT close FROM price_history
                   WHERE symbol = ? AND timestamp >= ? AND close IS NOT NULL
                   ORDER BY timestamp ASC LIMIT 1""",
                (symbol.upper(), since_date[:10]),
            ).fetchone()

            if row:
                sr.price_at_thesis_creation = row["close"] if isinstance(row, dict) else row[0]
            else:
                # Fall back to yfinance history
                period = _days_to_period(sr.period_days)
                history = get_history(symbol, period=period)
                if history:
                    # Find closest date to since_date
                    target = since_date[:10]
                    closest = min(history, key=lambda h: abs(
                        datetime.strptime(h["date"], "%Y-%m-%d").timestamp()
                        - datetime.strptime(target, "%Y-%m-%d").timestamp()
                    ))
                    sr.price_at_thesis_creation = closest["close"]

            # Get current price
            current = get_price(symbol)
            if "error" in current:
                sr.error = current["error"]
                return sr

            sr.current_price = current["price"]

            # Calculate return
            if sr.price_at_thesis_creation and sr.current_price:
                sr.return_pct = round(
                    (sr.current_price - sr.price_at_thesis_creation)
                    / sr.price_at_thesis_creation
                    * 100,
                    2,
                )

        except Exception as e:
            sr.error = str(e)

        return sr

    def score_thesis(self, thesis_id: int, fetch_prices: bool = True) -> ThesisScorecard | None:
        """Score a single thesis against actual returns.

        Args:
            thesis_id: The thesis to score.
            fetch_prices: If True, fetches live prices. If False, uses DB only.

        Returns:
            ThesisScorecard or None if thesis not found.
        """
        row = self.db.execute(
            "SELECT * FROM theses WHERE id = ?", (thesis_id,)
        ).fetchone()
        if not row:
            return None

        # Build scorecard — row is already a dict from dict_row_factory
        thesis = dict(row) if not isinstance(row, dict) else row

        symbols = self._parse_symbols(thesis.get("symbols"))
        conviction = float(thesis.get("conviction", 0) or 0)
        if conviction <= 1:
            conviction = conviction * 100
        created_at = thesis.get("created_at", "")

        sc = ThesisScorecard(
            thesis_id=thesis_id,
            title=thesis.get("title", ""),
            conviction=conviction,
            status=thesis.get("status", ""),
            symbols=symbols,
            created_at=created_at,
        )

        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=UTC)
                sc.age_days = (datetime.now(UTC) - created_dt).days
            except ValueError:
                pass

        # Get returns for each symbol
        if fetch_prices and symbols:
            for sym in symbols:
                sr = self._get_symbol_return(sym, created_at)
                sc.symbol_returns.append(sr)

            # Calculate aggregates
            valid_returns = [sr.return_pct for sr in sc.symbol_returns if sr.return_pct is not None]
            if valid_returns:
                sc.avg_return_pct = round(sum(valid_returns) / len(valid_returns), 2)
                best_sr = max(sc.symbol_returns, key=lambda s: s.return_pct or -9999)
                worst_sr = min(sc.symbol_returns, key=lambda s: s.return_pct or 9999)
                sc.best_symbol = best_sr.symbol
                sc.best_return_pct = best_sr.return_pct
                sc.worst_symbol = worst_sr.symbol
                sc.worst_return_pct = worst_sr.return_pct

                # Calibration: how well does conviction predict returns?
                # High conviction + positive returns = good calibration
                # High conviction + negative returns = bad calibration
                # Score 0-100 where 50 = neutral
                sc.calibration_score = _compute_calibration(
                    conviction, sc.avg_return_pct
                )

        return sc

    def score_all(self, fetch_prices: bool = True) -> list[ThesisScorecard]:
        """Score all active theses.

        Returns:
            List of ThesisScorecard objects.
        """
        rows = self.db.execute(
            "SELECT id FROM theses WHERE status IN ('active', 'draft') ORDER BY id"
        ).fetchall()

        scorecards = []
        for row in rows:
            thesis_id = row["id"] if isinstance(row, dict) else row[0]
            sc = self.score_thesis(thesis_id, fetch_prices=fetch_prices)
            if sc:
                scorecards.append(sc)

        return scorecards

    def persist_snapshot(self, scorecard: ThesisScorecard) -> None:
        """Save a scorecard snapshot to the database.

        Args:
            scorecard: The scorecard to persist.
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        try:
            self.db.execute(
                """INSERT OR REPLACE INTO outcome_snapshots
                   (thesis_id, snapshot_date, symbols, conviction,
                    avg_return_pct, best_symbol, best_return_pct,
                    worst_symbol, worst_return_pct, thesis_age_days,
                    calibration_score, details_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scorecard.thesis_id,
                    today,
                    json.dumps(scorecard.symbols),
                    scorecard.conviction,
                    scorecard.avg_return_pct,
                    scorecard.best_symbol,
                    scorecard.best_return_pct,
                    scorecard.worst_symbol,
                    scorecard.worst_return_pct,
                    scorecard.age_days,
                    scorecard.calibration_score,
                    json.dumps(scorecard.to_dict()),
                ),
            )
            self.db.connect().commit()
        except Exception as e:
            logger.warning("Failed to persist outcome snapshot: %s", e)

    def persist_all(self, scorecards: list[ThesisScorecard]) -> int:
        """Persist snapshots for all scorecards. Returns count saved."""
        saved = 0
        for sc in scorecards:
            try:
                self.persist_snapshot(sc)
                saved += 1
            except Exception:
                pass
        return saved

    def get_history(
        self, thesis_id: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        """Get historical outcome snapshots for a thesis.

        Returns list of snapshot dicts, newest first.
        """
        rows = self.db.execute(
            """SELECT * FROM outcome_snapshots
               WHERE thesis_id = ?
               ORDER BY snapshot_date DESC LIMIT ?""",
            (thesis_id, limit),
        ).fetchall()

        if not rows:
            return []

        return [dict(row) if not isinstance(row, dict) else row for row in rows]

    def format_summary(self, scorecards: list[ThesisScorecard]) -> str:
        """Format all scorecards into a summary message."""
        if not scorecards:
            return "📊 No theses to score."

        lines = ["📊 **Thesis Outcome Report**\n"]
        for sc in scorecards:
            lines.append(sc.format_telegram())
            lines.append("")

        # Overall stats
        valid = [sc for sc in scorecards if sc.avg_return_pct is not None]
        if valid:
            avg_all = sum(sc.avg_return_pct for sc in valid) / len(valid)  # type: ignore
            lines.append(f"**Portfolio avg: {avg_all:+.1f}%**")
            cal_scores = [sc.calibration_score for sc in valid if sc.calibration_score is not None]
            if cal_scores:
                avg_cal = sum(cal_scores) / len(cal_scores)
                lines.append(f"**Avg calibration: {avg_cal:.0f}/100**")

        return "\n".join(lines)


def _compute_calibration(conviction: float, avg_return: float) -> float:
    """Compute calibration score (0-100).

    Measures alignment between conviction level and actual returns.
    - High conviction + strong positive return → high score
    - High conviction + negative return → low score
    - Low conviction + flat return → neutral (decent calibration)

    Args:
        conviction: Conviction percentage (0-100).
        avg_return: Average return percentage.

    Returns:
        Calibration score 0-100.
    """
    # Normalize conviction to -1..1 scale centered at 50
    conv_signal = (conviction - 50) / 50  # -1 to 1

    # Normalize return — cap at ±50% for scoring
    ret_signal = max(-50, min(50, avg_return)) / 50  # -1 to 1

    # Alignment: both positive or both negative = good
    alignment = conv_signal * ret_signal  # -1 to 1

    # Convert to 0-100 scale
    score = 50 + (alignment * 50)
    return round(max(0, min(100, score)), 1)


def _days_to_period(days: int) -> str:
    """Convert number of days to a yfinance period string."""
    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    return "5y"
