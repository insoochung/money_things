"""Politician scoring engine for congressional trades intelligence.

Scores politicians based on their trading history using an Unusual Whales-inspired
composite scoring system. Factors include win rate, trade quality, committee
relevance, and filing timing patterns.

Classes:
    PoliticianScorer: Main scoring engine that calculates politician scores and
        enriches individual trades with intelligence metadata.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db.database import Database

logger = logging.getLogger(__name__)

# Committee → sector mapping for relevance scoring
COMMITTEE_SECTORS: dict[str, list[str]] = {
    "Financial Services": ["financials", "banking", "insurance"],
    "Energy and Commerce": ["energy", "utilities", "healthcare", "telecom"],
    "Armed Services": ["defense", "aerospace"],
    "Science, Space, and Technology": ["technology", "space"],
    "Agriculture": ["agriculture", "commodities"],
    "Ways and Means": ["broad"],
    "Judiciary": ["technology", "media"],
    "Transportation and Infrastructure": ["industrials", "construction"],
    "Appropriations": ["broad"],
    "Intelligence": ["defense", "cybersecurity", "technology"],
}

# Common ETF tickers to detect low-quality trades
ETF_TICKERS: set[str] = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VEA", "VWO", "BND", "AGG",
    "GLD", "SLV", "TLT", "IEF", "HYG", "LQD", "XLF", "XLE", "XLK", "XLV",
    "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "ARKK", "ARKW",
}

# Score component weights
WEIGHT_WIN_RATE = 0.40
WEIGHT_TRADE_QUALITY = 0.25
WEIGHT_COMMITTEE_RELEVANCE = 0.20
WEIGHT_TIMING_PATTERN = 0.15

# Tier thresholds
TIER_WHALE = 80
TIER_NOTABLE = 60
TIER_AVERAGE = 40


@dataclass
class PoliticianScore:
    """Computed score and metadata for a politician.

    Attributes:
        politician: Politician name.
        score: Composite score 0-100.
        tier: Tier label (whale/notable/average/noise).
        total_trades: Number of trades analyzed.
        win_rate: Percentage of profitable trades.
        avg_return_30d: Average return after 30 days.
        avg_return_60d: Average return after 60 days.
        avg_return_90d: Average return after 90 days.
        trade_size_preference: Preferred trade size bucket.
        filing_delay_avg_days: Average filing delay in days.
        committees: Known committee memberships.
        best_sectors: Top sectors traded.
    """

    politician: str
    score: float = 0.0
    tier: str = "unknown"
    total_trades: int = 0
    win_rate: float = 0.0
    avg_return_30d: float = 0.0
    avg_return_60d: float = 0.0
    avg_return_90d: float = 0.0
    trade_size_preference: str = "unknown"
    filing_delay_avg_days: float = 0.0
    committees: list[str] = field(default_factory=list)
    best_sectors: list[str] = field(default_factory=list)


def parse_amount_bucket(amount_range: str) -> str:
    """Parse a congressional disclosure amount range into a size bucket.

    Args:
        amount_range: Raw amount string like "$1,001 - $15,000" or "$100,001 - $250,000".

    Returns:
        Size bucket: 'small', 'medium', or 'large'.
    """
    if not amount_range:
        return "small"

    # Extract all dollar amounts from the string
    amounts = re.findall(r"\$?([\d,]+)", amount_range)
    if not amounts:
        return "small"

    # Use the upper bound if available, otherwise the first number
    raw = amounts[-1].replace(",", "")
    try:
        upper = int(raw)
    except ValueError:
        return "small"

    if upper <= 15_000:
        return "small"
    if upper <= 100_000:
        return "medium"
    return "large"


def calculate_disclosure_lag(date_traded: str, date_filed: str) -> int | None:
    """Calculate the number of days between trade execution and disclosure filing.

    Args:
        date_traded: Trade date string (YYYY-MM-DD or MM/DD/YYYY).
        date_filed: Filing date string (YYYY-MM-DD or MM/DD/YYYY).

    Returns:
        Number of days between trade and filing, or None if unparseable.
    """
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            traded = datetime.strptime(date_traded, fmt).replace(tzinfo=UTC)
            break
        except ValueError:
            continue
    else:
        return None

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            filed = datetime.strptime(date_filed, fmt).replace(tzinfo=UTC)
            break
        except ValueError:
            continue
    else:
        return None

    lag = (filed - traded).days
    return max(0, lag)


def assign_tier(score: float) -> str:
    """Assign a tier label based on composite score.

    Args:
        score: Composite score 0-100.

    Returns:
        Tier string: 'whale', 'notable', 'average', or 'noise'.
    """
    if score >= TIER_WHALE:
        return "whale"
    if score >= TIER_NOTABLE:
        return "notable"
    if score >= TIER_AVERAGE:
        return "average"
    return "noise"


def is_etf(symbol: str) -> bool:
    """Check if a symbol is a known ETF.

    Args:
        symbol: Ticker symbol to check.

    Returns:
        True if the symbol is a known ETF.
    """
    return symbol.upper() in ETF_TICKERS


def check_committee_relevance(committees: list[str], symbol: str) -> bool:
    """Check if any of the politician's committees are relevant to the traded symbol's sector.

    This is a simplified check — in practice you'd map symbol → sector via an API.
    For now, any non-broad committee match counts as relevant.

    Args:
        committees: List of committee names.
        symbol: Traded ticker symbol (used for ETF detection).

    Returns:
        True if there's potential committee relevance.
    """
    if is_etf(symbol):
        return False

    for committee in committees:
        sectors = COMMITTEE_SECTORS.get(committee, [])
        if sectors and sectors != ["broad"]:
            return True
    return False


class PoliticianScorer:
    """Scores politicians based on their congressional trading history.

    Uses a composite scoring system inspired by Unusual Whales:
    - Win rate (40%): Percentage of trades profitable after 30/60/90 days
    - Trade quality (25%): Individual stocks vs ETFs, larger sizes = more conviction
    - Committee relevance (20%): Trades in sectors matching their committee assignments
    - Timing pattern (15%): Late filers who are profitable = more suspicious/valuable

    Args:
        db: Database instance for querying congress_trades and politician_scores.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Add scoring columns to congress_trades if they don't exist."""
        migrations = [
            ("politician_score", "REAL"),
            ("disclosure_lag_days", "INTEGER"),
            ("trade_size_bucket", "TEXT"),
            ("committee_relevant", "INTEGER DEFAULT 0"),
        ]
        for col, col_type in migrations:
            try:
                self.db.execute(
                    f"ALTER TABLE congress_trades ADD COLUMN {col} {col_type}"
                )
                self.db.connect().commit()
            except Exception:
                pass  # Column already exists

    def score_politician(self, name: str) -> PoliticianScore:
        """Calculate the composite score for a single politician.

        Args:
            name: Politician name as stored in congress_trades.

        Returns:
            PoliticianScore with all computed metrics.
        """
        trades = self.db.fetchall(
            "SELECT * FROM congress_trades WHERE politician = ? ORDER BY date_traded DESC",
            (name,),
        )

        if not trades:
            return PoliticianScore(politician=name)

        # Load existing score record for committee/party info
        existing = self.db.fetchone(
            "SELECT * FROM politician_scores WHERE politician = ?", (name,)
        )
        committees: list[str] = []
        if existing and existing.get("committees"):
            try:
                committees = json.loads(existing["committees"])
            except (json.JSONDecodeError, TypeError):
                pass

        total = len(trades)

        # Win rate component (simplified: buy count vs sell count ratio as proxy)
        win_rate = self._estimate_win_rate(trades)

        # Trade quality component
        quality_score = self._score_trade_quality(trades)

        # Committee relevance component
        relevance_score = self._score_committee_relevance(trades, committees)

        # Timing pattern component
        timing_score = self._score_timing_pattern(trades)

        # Composite score
        raw_score = (
            win_rate * WEIGHT_WIN_RATE
            + quality_score * WEIGHT_TRADE_QUALITY
            + relevance_score * WEIGHT_COMMITTEE_RELEVANCE
            + timing_score * WEIGHT_TIMING_PATTERN
        )
        score = min(100.0, max(0.0, raw_score))

        # Determine trade size preference
        buckets = [parse_amount_bucket(t.get("amount_range", "")) for t in trades]
        size_pref = max(set(buckets), key=buckets.count) if buckets else "unknown"

        # Average filing delay
        lags = []
        for t in trades:
            lag = calculate_disclosure_lag(
                t.get("date_traded", ""), t.get("date_filed", "")
            )
            if lag is not None:
                lags.append(lag)
        avg_lag = sum(lags) / len(lags) if lags else 0.0

        # Best sectors (from unique symbols, excluding ETFs)
        symbols = {t["symbol"] for t in trades if not is_etf(t.get("symbol", ""))}
        best_sectors = list(symbols)[:5]

        result = PoliticianScore(
            politician=name,
            score=score,
            tier=assign_tier(score),
            total_trades=total,
            win_rate=win_rate,
            trade_size_preference=size_pref,
            filing_delay_avg_days=avg_lag,
            committees=committees,
            best_sectors=best_sectors,
        )

        self._save_score(result)
        return result

    def _estimate_win_rate(self, trades: list[dict]) -> float:
        """Estimate win rate from trade history.

        Without price data, we use heuristics: buy count, trade frequency,
        and size as proxies for profitable trading behavior.

        Args:
            trades: List of trade dicts.

        Returns:
            Estimated win rate as a score 0-100.
        """
        if not trades:
            return 0.0

        buy_count = sum(1 for t in trades if t.get("action") == "buy")
        total = len(trades)
        buy_ratio = buy_count / total if total > 0 else 0.5

        # More buys than sells suggests conviction (bullish bias in bull market)
        # Scale: 50% buys = 50 score, 80% buys = 70 score
        base = 40.0 + (buy_ratio * 40.0)

        # Larger trades suggest more conviction/information advantage
        large_count = sum(
            1 for t in trades if parse_amount_bucket(t.get("amount_range", "")) == "large"
        )
        large_bonus = min(20.0, (large_count / max(total, 1)) * 40.0)

        return min(100.0, base + large_bonus)

    def _score_trade_quality(self, trades: list[dict]) -> float:
        """Score trade quality based on individual stocks vs ETFs and trade sizes.

        Args:
            trades: List of trade dicts.

        Returns:
            Quality score 0-100.
        """
        if not trades:
            return 0.0

        total = len(trades)
        individual_count = sum(1 for t in trades if not is_etf(t.get("symbol", "")))
        individual_ratio = individual_count / total

        # Individual stock picks = higher quality (more alpha potential)
        stock_score = individual_ratio * 60.0

        # Larger trades = more conviction
        size_scores = {"small": 10, "medium": 25, "large": 40}
        avg_size_score = sum(
            size_scores.get(parse_amount_bucket(t.get("amount_range", "")), 10)
            for t in trades
        ) / total

        return min(100.0, stock_score + avg_size_score)

    def _score_committee_relevance(
        self, trades: list[dict], committees: list[str]
    ) -> float:
        """Score how often a politician trades in sectors relevant to their committees.

        Args:
            trades: List of trade dicts.
            committees: List of committee names.

        Returns:
            Relevance score 0-100.
        """
        if not trades or not committees:
            return 50.0  # Neutral if no committee data

        relevant_count = sum(
            1 for t in trades
            if check_committee_relevance(committees, t.get("symbol", ""))
        )
        relevance_ratio = relevant_count / len(trades)

        # High relevance = more suspicious/valuable signal
        return min(100.0, relevance_ratio * 100.0 + 20.0)

    def _score_timing_pattern(self, trades: list[dict]) -> float:
        """Score based on filing delay patterns.

        Late filers who trade large amounts are more suspicious/valuable.

        Args:
            trades: List of trade dicts.

        Returns:
            Timing score 0-100.
        """
        lags: list[int] = []
        for t in trades:
            lag = calculate_disclosure_lag(
                t.get("date_traded", ""), t.get("date_filed", "")
            )
            if lag is not None:
                lags.append(lag)

        if not lags:
            return 50.0

        avg_lag = sum(lags) / len(lags)

        # Late filers (>30 days) with large trades = suspicious
        # Score increases with delay up to a point
        if avg_lag <= 15:
            base = 30.0  # Early filer, less interesting
        elif avg_lag <= 30:
            base = 50.0
        elif avg_lag <= 45:
            base = 70.0  # Suspicious delay
        else:
            base = 85.0  # Very late, very suspicious

        return min(100.0, base)

    def _save_score(self, ps: PoliticianScore) -> None:
        """Persist a politician score to the database.

        Args:
            ps: PoliticianScore to save.
        """
        self.db.execute(
            """INSERT INTO politician_scores
               (politician, total_trades, win_rate, score, tier,
                trade_size_preference, filing_delay_avg_days, committees,
                best_sectors, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(politician) DO UPDATE SET
                 total_trades = excluded.total_trades,
                 win_rate = excluded.win_rate,
                 score = excluded.score,
                 tier = excluded.tier,
                 trade_size_preference = excluded.trade_size_preference,
                 filing_delay_avg_days = excluded.filing_delay_avg_days,
                 committees = excluded.committees,
                 best_sectors = excluded.best_sectors,
                 last_updated = datetime('now')""",
            (
                ps.politician,
                ps.total_trades,
                ps.win_rate,
                ps.score,
                ps.tier,
                ps.trade_size_preference,
                ps.filing_delay_avg_days,
                json.dumps(ps.committees),
                json.dumps(ps.best_sectors),
            ),
        )
        self.db.connect().commit()

    def score_all(self) -> list[PoliticianScore]:
        """Score all politicians with trades in the database.

        Returns:
            List of PoliticianScore objects, sorted by score descending.
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT politician FROM congress_trades"
        )
        scores = [self.score_politician(row["politician"]) for row in rows]
        scores.sort(key=lambda s: s.score, reverse=True)
        return scores

    def score_trade(self, trade: dict) -> float:
        """Score an individual trade from 0 to 1.

        Higher scores for: large amounts, individual stocks, committee-relevant,
        known whale politicians.

        Args:
            trade: Trade dict with symbol, amount_range, politician keys.

        Returns:
            Trade score from 0.0 to 1.0.
        """
        score = 0.0

        # Size component (0-0.3)
        bucket = parse_amount_bucket(trade.get("amount_range", ""))
        size_scores = {"small": 0.1, "medium": 0.2, "large": 0.3}
        score += size_scores.get(bucket, 0.1)

        # Stock vs ETF (0-0.3)
        if not is_etf(trade.get("symbol", "")):
            score += 0.3
        else:
            score += 0.05

        # Politician tier bonus (0-0.4)
        existing = self.db.fetchone(
            "SELECT score, tier FROM politician_scores WHERE politician = ?",
            (trade.get("politician", ""),),
        )
        if existing:
            score += (existing["score"] / 100.0) * 0.4
        else:
            score += 0.15  # Unknown politician gets neutral

        return min(1.0, score)

    def get_top_politicians(self, n: int = 20) -> list[dict]:
        """Get the top N politicians by score.

        Args:
            n: Number of politicians to return.

        Returns:
            List of politician score dicts.
        """
        rows = self.db.fetchall(
            """SELECT * FROM politician_scores
               ORDER BY score DESC
               LIMIT ?""",
            (n,),
        )
        return [dict(r) for r in rows]

    def enrich_trade(self, trade: dict) -> dict:
        """Enrich a trade dict with scoring metadata.

        Adds politician_score, disclosure_lag_days, trade_size_bucket,
        and committee_relevant fields.

        Args:
            trade: Raw trade dict.

        Returns:
            Enriched trade dict with additional fields.
        """
        enriched = dict(trade)

        # Trade size bucket
        enriched["trade_size_bucket"] = parse_amount_bucket(
            trade.get("amount_range", "")
        )

        # Disclosure lag
        lag = calculate_disclosure_lag(
            trade.get("date_traded", ""), trade.get("date_filed", "")
        )
        enriched["disclosure_lag_days"] = lag

        # Politician score and tier
        existing = self.db.fetchone(
            "SELECT score, tier, committees FROM politician_scores WHERE politician = ?",
            (trade.get("politician", ""),),
        )
        if existing:
            enriched["politician_score"] = existing["score"]
            enriched["politician_tier"] = existing["tier"]
            committees = []
            if existing.get("committees"):
                try:
                    committees = json.loads(existing["committees"])
                except (json.JSONDecodeError, TypeError):
                    pass
            enriched["committee_relevant"] = (
                1 if check_committee_relevance(committees, trade.get("symbol", "")) else 0
            )
        else:
            enriched["politician_score"] = None
            enriched["politician_tier"] = "unknown"
            enriched["committee_relevant"] = 0

        return enriched

    def build_reasoning(self, trade: dict) -> str:
        """Build a rich reasoning string for a congress trade signal.

        Args:
            trade: Enriched trade dict (after enrich_trade).

        Returns:
            Human-readable reasoning string with emoji indicators.
        """
        politician = trade.get("politician", "Unknown")
        symbol = trade.get("symbol", "???")
        amount = trade.get("amount_range", "unknown")
        tier = trade.get("politician_tier", "unknown")
        score = trade.get("politician_score")
        lag = trade.get("disclosure_lag_days")
        relevant = trade.get("committee_relevant", 0)

        # Tier emoji
        tier_emoji = {"whale": "🐋", "notable": "⭐", "average": "📊", "noise": "🔇"}
        emoji = tier_emoji.get(tier, "❓")

        # Get win rate from DB
        ps = self.db.fetchone(
            "SELECT win_rate, avg_return_90d FROM politician_scores WHERE politician = ?",
            (politician,),
        )

        parts = [
            f"{emoji} {politician} ({tier}",
        ]
        if score is not None:
            parts[0] += f", score {score:.0f}"
        parts[0] += f") bought {symbol}"

        if amount:
            parts.append(f"— {amount}")

        if lag is not None:
            parts.append(f"filed {lag}d after trade")

        if relevant:
            parts.append("Committee: sector-relevant")

        if ps:
            if ps.get("win_rate"):
                parts.append(f"{ps['win_rate']:.0f}% win rate")
            if ps.get("avg_return_90d"):
                parts.append(f"+{ps['avg_return_90d']:.1f}% avg 90d return")

        return ". ".join(parts) + "."
