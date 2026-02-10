"""Signal generation pipeline: autonomous thesis evaluation and signal creation.

Evaluates active theses against current market data and generates actionable
trading signals. Uses multi-factor confidence scoring with blocking conditions
for earnings, trading windows, thesis maturity, and conviction gates.

The confidence scoring pipeline uses six weighted factors:
    - Thesis conviction (30%)
    - Watchlist trigger hit (20%)
    - News sentiment (15%)
    - Critic assessment (15%)
    - Calibration / win rate (10%)
    - Congress alignment (10%)

Blocking conditions (signal is not generated if any are true):
    - Earnings within 5 days
    - Trading window blackout (e.g., META for Insoo)
    - Thesis age < 1 week
    - Thesis conviction < 70%
    - Fewer than 2 /think sessions on the thesis

Classes:
    SignalGenerator: Evaluates theses and generates trading signals.
    MultiFactorScore: Pydantic model for the multi-factor confidence breakdown.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from db.database import Database
from engine import Signal, SignalAction, SignalSource, SignalStatus, ThesisStatus
from engine.congress_scoring import PoliticianScorer
from engine.earnings_calendar import is_earnings_imminent
from engine.risk import RiskManager
from engine.signals import SignalEngine
from engine.thesis import ThesisEngine

logger = logging.getLogger(__name__)

# Thesis statuses that can generate BUY signals
_BUY_STATUSES = {
    ThesisStatus.ACTIVE,
    ThesisStatus.STRENGTHENING,
    ThesisStatus.CONFIRMED,
}

# Thesis statuses that should generate SELL signals for held positions
_SELL_STATUSES = {
    ThesisStatus.WEAKENING,
    ThesisStatus.INVALIDATED,
}

# Minimum daily price move (%) to trigger a price-based signal
_DAILY_MOVE_THRESHOLD = 2.0
# Minimum weekly price move (%) to trigger a price-based signal
_WEEKLY_MOVE_THRESHOLD = 5.0
# Base position size as fraction of NAV
_BASE_POSITION_SIZE = 0.02
# Default user_id for single-user system
_DEFAULT_USER_ID = 1

# Multi-factor weights (must sum to 1.0)
_WEIGHT_THESIS_CONVICTION = 0.30
_WEIGHT_WATCHLIST_TRIGGER = 0.20
_WEIGHT_NEWS_SENTIMENT = 0.15
_WEIGHT_CRITIC_ASSESSMENT = 0.15
_WEIGHT_CALIBRATION = 0.10
_WEIGHT_CONGRESS_ALIGNMENT = 0.10

# Blocking thresholds
_MIN_CONVICTION = 0.70
_MIN_THINK_SESSIONS = 2
_MIN_THESIS_AGE_DAYS = 7


class MultiFactorScore(BaseModel):
    """Breakdown of the multi-factor confidence scoring.

    Each factor is a normalized 0.0–1.0 score. The weighted_total
    is the sum of (factor * weight) across all factors.

    Attributes:
        thesis_conviction: Raw thesis conviction score.
        watchlist_trigger: 1.0 if a trigger was hit, 0.0 otherwise.
        news_sentiment: Average news sentiment score for the symbol.
        critic_assessment: Last critic assessment score (0.0–1.0).
        calibration: Historical win rate for this signal source.
        congress_alignment: Congress trade alignment score.
        weighted_total: Final weighted confidence score.
        blocked: Whether the signal was blocked.
        block_reason: Reason for blocking, if any.
    """

    thesis_conviction: float = 0.0
    watchlist_trigger: float = 0.0
    news_sentiment: float = 0.5
    critic_assessment: float = 0.5
    calibration: float = 0.5
    congress_alignment: float = 0.5
    weighted_total: float = 0.0
    blocked: bool = False
    block_reason: str = ""


class SignalGenerator:
    """Generates trading signals by evaluating theses against market data.

    Scans all active/non-archived theses, checks each symbol for price
    movements and portfolio state, then generates BUY or SELL signals with
    multi-factor confidence scoring and risk checks.

    Attributes:
        db: Database instance.
        signal_engine: For creating and listing signals.
        thesis_engine: For listing theses.
        risk_manager: For pre-trade risk checks and NAV.
        pricing: The engine.pricing module for fetching prices.
        user_id: User ID for risk checks (default 1).
    """

    def __init__(
        self,
        db: Database,
        signal_engine: SignalEngine,
        thesis_engine: ThesisEngine,
        risk_manager: RiskManager,
        pricing: Any,
        user_id: int = _DEFAULT_USER_ID,
    ) -> None:
        self.db = db
        self.signal_engine = signal_engine
        self.thesis_engine = thesis_engine
        self.risk_manager = risk_manager
        self.pricing = pricing
        self.user_id = user_id
        self._politician_scorer: PoliticianScorer | None = None

    def run_scan(self) -> list[dict]:
        """Main scan loop. Called by scheduler.

        For each non-archived thesis:
        1. Check blocking conditions (earnings, windows, maturity)
        2. Evaluate thesis symbols for potential signals
        3. Score confidence using multi-factor pipeline
        4. Run pre-trade risk checks
        5. Create signals that pass all checks

        Returns:
            List of dicts describing generated signals.
        """
        logger.info("signal_scan: starting scan")
        generated: list[dict] = []

        all_theses = self.thesis_engine.list_theses()
        active_theses = [
            t for t in all_theses
            if t.status != ThesisStatus.ARCHIVED
        ]

        if not active_theses:
            logger.info("signal_scan: no active theses to evaluate")
            return generated

        for thesis in active_theses:
            try:
                signals = self._evaluate_thesis_symbols(thesis)
                generated.extend(signals)
            except Exception:
                logger.exception(
                    "signal_scan: error evaluating thesis %d (%s)",
                    thesis.id,
                    thesis.title,
                )

        logger.info("signal_scan: generated %d signals", len(generated))
        return generated

    def _evaluate_thesis_symbols(self, thesis: Any) -> list[dict]:
        """Evaluate each symbol in a thesis for potential signals.

        Args:
            thesis: Thesis model from ThesisEngine.

        Returns:
            List of signal result dicts.
        """
        if not thesis.symbols:
            return []

        results: list[dict] = []
        held_symbols = self._get_held_symbols()
        pending_symbols = self._get_pending_symbols()

        for symbol in thesis.symbols:
            if symbol in pending_symbols:
                logger.debug(
                    "signal_scan: skipping %s — pending signal exists",
                    symbol,
                )
                continue

            trigger = self._check_price_triggers(symbol, thesis)

            if thesis.status in _BUY_STATUSES and symbol not in held_symbols:
                if trigger or thesis.status in {
                    ThesisStatus.STRENGTHENING,
                    ThesisStatus.CONFIRMED,
                }:
                    result = self._try_generate_signal(
                        "BUY", symbol, thesis, trigger,
                    )
                    if result:
                        results.append(result)

            elif thesis.status in _SELL_STATUSES and symbol in held_symbols:
                result = self._try_generate_signal(
                    "SELL", symbol, thesis, trigger,
                )
                if result:
                    results.append(result)

        return results

    def _try_generate_signal(
        self,
        action: str,
        symbol: str,
        thesis: Any,
        trigger: dict | None,
    ) -> dict | None:
        """Attempt to generate a signal after checking blocks and scoring.

        Runs all blocking checks, then multi-factor scoring, then risk
        checks before creating the signal.

        Args:
            action: "BUY" or "SELL".
            symbol: Ticker symbol.
            thesis: Thesis model.
            trigger: Price trigger dict or None.

        Returns:
            Dict describing the created signal, or None if blocked.
        """
        # Check blocking conditions (skip for SELL — we want to exit)
        if action == "BUY":
            block_reason = self._check_blocking_conditions(
                symbol, thesis,
            )
            if block_reason:
                logger.info(
                    "signal_scan: %s %s blocked: %s",
                    action, symbol, block_reason,
                )
                return None

        mf_score = self._compute_multi_factor_score(
            symbol, thesis, trigger,
        )

        # For SELL signals, use inverse conviction (urgency to exit)
        if action == "SELL":
            sell_urgency = 1.0 - mf_score.critic_assessment
            raw_confidence = max(mf_score.weighted_total, sell_urgency)
        else:
            raw_confidence = mf_score.weighted_total

        reasoning = self._build_reasoning(
            action, symbol, thesis, trigger, mf_score,
        )

        return self._generate_signal(
            action, symbol, thesis, raw_confidence, reasoning,
        )

    def _check_blocking_conditions(
        self,
        symbol: str,
        thesis: Any,
    ) -> str | None:
        """Check all blocking conditions for a BUY signal.

        Args:
            symbol: Ticker symbol.
            thesis: Thesis model.

        Returns:
            Block reason string if blocked, None if clear.
        """
        # Check thesis-related conditions
        thesis_block = self._check_thesis_conditions(thesis)
        if thesis_block:
            return thesis_block

        # Check market-related conditions
        market_block = self._check_market_conditions(symbol)
        if market_block:
            return market_block

        return None

    def _check_thesis_conditions(self, thesis: Any) -> str | None:
        """Check thesis-specific blocking conditions."""
        # Conviction gate
        conviction = thesis.conviction if thesis.conviction else 0.0
        if conviction < _MIN_CONVICTION:
            return f"conviction {conviction:.0%} < {_MIN_CONVICTION:.0%}"

        # Thesis age gate
        age_block = self._check_thesis_age(thesis)
        if age_block:
            return age_block

        # Think sessions gate
        session_count = self._get_think_session_count(thesis.id)
        if session_count < _MIN_THINK_SESSIONS:
            return (
                f"only {session_count} /think sessions "
                f"< {_MIN_THINK_SESSIONS} minimum"
            )

        return None

    def _check_thesis_age(self, thesis: Any) -> str | None:
        """Check if thesis is old enough."""
        if not thesis.created_at:
            return None

        try:
            created = datetime.fromisoformat(str(thesis.created_at))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age = datetime.now(UTC) - created
            if age < timedelta(days=_MIN_THESIS_AGE_DAYS):
                return (
                    f"thesis age {age.days}d "
                    f"< {_MIN_THESIS_AGE_DAYS}d minimum"
                )
        except (ValueError, TypeError):
            pass

        return None

    def _check_market_conditions(self, symbol: str) -> str | None:
        """Check market-related blocking conditions."""
        # Earnings calendar block
        if is_earnings_imminent(symbol):
            return f"{symbol} earnings imminent (within 5 days)"

        # Trading window blackout
        if self._is_in_trading_blackout(symbol):
            return f"{symbol} in trading window blackout"

        return None

    def _get_think_session_count(self, thesis_id: int) -> int:
        """Get the number of /think sessions for a thesis.

        Checks the thoughts.sessions table via the moves DB (the bridge
        may have synced session counts), or falls back to checking
        thesis_versions as a proxy.

        Args:
            thesis_id: Thesis ID.

        Returns:
            Number of think sessions.
        """
        # Try thesis_versions as a proxy for think sessions
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM thesis_versions "
            "WHERE thesis_id = ?",
            (thesis_id,),
        )
        return row["cnt"] if row else 0

    def _is_in_trading_blackout(self, symbol: str) -> bool:
        """Check if a symbol is in a trading window blackout.

        Looks at the trading_windows table for windows where the current
        date falls between opens and closes dates.

        Args:
            symbol: Ticker symbol.

        Returns:
            True if the symbol is currently blacked out.
        """
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        # Check if there's an OPEN window right now
        open_window = self.db.fetchone(
            """SELECT id FROM trading_windows
               WHERE symbol = ?
                 AND opens IS NOT NULL
                 AND closes IS NOT NULL
                 AND ? >= opens AND ? <= closes
            """,
            (symbol.upper(), now, now),
        )
        # If no open window exists but windows are defined → blacked out
        has_any_window = self.db.fetchone(
            "SELECT id FROM trading_windows WHERE symbol = ?",
            (symbol.upper(),),
        )
        if has_any_window and not open_window:
            return True
        return False

    def _compute_multi_factor_score(
        self,
        symbol: str,
        thesis: Any,
        trigger: dict | None,
    ) -> MultiFactorScore:
        """Compute the multi-factor confidence score.

        Combines six weighted factors into a single confidence score.

        Args:
            symbol: Ticker symbol.
            thesis: Thesis model.
            trigger: Price trigger dict or None.

        Returns:
            MultiFactorScore with individual factor scores and total.
        """
        # 1. Thesis conviction (0.0–1.0)
        conviction = thesis.conviction if thesis.conviction else 0.5

        # 2. Watchlist trigger (1.0 if hit, 0.0 if not)
        watchlist_score = self._check_watchlist_triggers(symbol)

        # 3. News sentiment (0.0–1.0)
        news_score = self._get_news_sentiment(symbol, thesis)

        # 4. Critic assessment (0.0–1.0)
        critic_score = self._get_critic_assessment(thesis)

        # 5. Calibration / win rate (0.0–1.0)
        calibration = self._get_calibration_score()

        # 6. Congress alignment (0.0–1.0)
        congress_score = self._get_congress_alignment(symbol)

        # Price trigger boost (additive on conviction, up to +0.15)
        if trigger:
            abs_move = abs(trigger.get("change_percent", 0))
            boost = min(abs_move / 20.0, 0.15)
            conviction = min(conviction + boost, 1.0)

        weighted = (
            conviction * _WEIGHT_THESIS_CONVICTION
            + watchlist_score * _WEIGHT_WATCHLIST_TRIGGER
            + news_score * _WEIGHT_NEWS_SENTIMENT
            + critic_score * _WEIGHT_CRITIC_ASSESSMENT
            + calibration * _WEIGHT_CALIBRATION
            + congress_score * _WEIGHT_CONGRESS_ALIGNMENT
        )

        return MultiFactorScore(
            thesis_conviction=conviction,
            watchlist_trigger=watchlist_score,
            news_sentiment=news_score,
            critic_assessment=critic_score,
            calibration=calibration,
            congress_alignment=congress_score,
            weighted_total=round(min(1.0, max(0.0, weighted)), 4),
        )

    def _check_watchlist_triggers(self, symbol: str) -> float:
        """Check if any active watchlist triggers have been hit.

        A trigger is considered "hit" if it has a triggered_at timestamp.

        Args:
            symbol: Ticker symbol.

        Returns:
            1.0 if any trigger was hit, 0.0 otherwise.
        """
        try:
            row = self.db.fetchone(
                """SELECT id FROM watchlist_triggers
                   WHERE symbol = ?
                     AND active = 1
                     AND triggered_at IS NOT NULL
                   LIMIT 1""",
                (symbol.upper(),),
            )
            return 1.0 if row else 0.0
        except Exception:
            # Table may not exist yet
            return 0.0

    def _get_news_sentiment(
        self, symbol: str, thesis: Any,
    ) -> float:
        """Get recent news sentiment score for a symbol's thesis.

        Checks the thesis_news table for articles from the last 7 days
        and computes a sentiment score based on supporting vs contradicting.

        Args:
            symbol: Ticker symbol.
            thesis: Thesis model.

        Returns:
            Sentiment score 0.0–1.0 (0.5 = neutral).
        """
        week_ago = (
            datetime.now(UTC) - timedelta(days=7)
        ).strftime("%Y-%m-%d")

        row = self.db.fetchone(
            """SELECT
                 SUM(CASE WHEN sentiment = 'supporting' THEN 1 ELSE 0
                     END) as sup,
                 SUM(CASE WHEN sentiment = 'contradicting' THEN 1
                     ELSE 0 END) as con,
                 COUNT(*) as total
               FROM thesis_news
               WHERE thesis_id = ? AND timestamp >= ?""",
            (thesis.id, week_ago),
        )

        if not row or not row["total"]:
            return 0.5  # Neutral if no news

        sup = row["sup"] or 0
        con = row["con"] or 0
        total = row["total"]

        # Scale: all supporting = 1.0, all contradicting = 0.0
        if total == 0:
            return 0.5
        return round((sup - con + total) / (2 * total), 4)

    def _get_critic_assessment(self, thesis: Any) -> float:
        """Get the last critic assessment score for a thesis.

        Looks at thesis_versions for critic-related transitions as a
        proxy. In the future, this will read from a dedicated critic
        assessment table.

        Args:
            thesis: Thesis model.

        Returns:
            Critic score 0.0–1.0 (0.5 = neutral).
        """
        # Use thesis status as a proxy for critic assessment
        status_scores = {
            ThesisStatus.CONFIRMED: 0.9,
            ThesisStatus.STRENGTHENING: 0.75,
            ThesisStatus.ACTIVE: 0.5,
            ThesisStatus.WEAKENING: 0.25,
            ThesisStatus.INVALIDATED: 0.1,
            ThesisStatus.ARCHIVED: 0.0,
        }
        return status_scores.get(thesis.status, 0.5)

    def _get_calibration_score(self) -> float:
        """Get the calibration score based on historical win rate.

        Reads from signal_scores table for the thesis_update source type.

        Returns:
            Calibration score 0.0–1.0 (0.5 if no history).
        """
        row = self.db.fetchone(
            """SELECT wins, total FROM signal_scores
               WHERE source_type = ?""",
            (SignalSource.THESIS_UPDATE.value,),
        )

        if not row or not row["total"]:
            return 0.5  # Neutral if no history

        win_rate = row["wins"] / row["total"]
        return round(min(1.0, win_rate), 4)

    @property
    def politician_scorer(self) -> PoliticianScorer:
        """Lazy-init PoliticianScorer (avoids table creation in tests that don't need it)."""
        if self._politician_scorer is None:
            self._politician_scorer = PoliticianScorer(self.db)
        return self._politician_scorer

    def _get_congress_alignment(self, symbol: str) -> float:
        """Get congress trade alignment score for a symbol.

        Uses PoliticianScorer.score_trade() to weight each recent congress
        trade by politician quality, trade size, committee relevance, and
        stock-vs-ETF. Falls back to neutral 0.5 if no data.

        Args:
            symbol: Ticker symbol.

        Returns:
            Congress alignment score 0.0–1.0 (0.5 if no data).
        """
        ninety_days_ago = (
            datetime.now(UTC) - timedelta(days=90)
        ).strftime("%Y-%m-%d")

        rows = self.db.fetchall(
            """SELECT ct.*
               FROM congress_trades ct
               WHERE ct.symbol = ?
                 AND ct.date_traded >= ?""",
            (symbol.upper(), ninety_days_ago),
        )

        if not rows:
            return 0.5  # Neutral

        try:
            scorer = self.politician_scorer
        except Exception:
            logger.debug("congress_scoring unavailable, returning neutral")
            return 0.5

        buy_signal = 0.0
        total_weight = 0.0
        for row in rows:
            trade = dict(row)
            trade_score = scorer.score_trade(trade)
            if trade.get("action") == "buy":
                buy_signal += trade_score
            else:
                buy_signal -= trade_score * 0.5
            total_weight += trade_score

        if total_weight == 0:
            return 0.5

        # Normalize to 0.0–1.0
        raw = 0.5 + (buy_signal / total_weight) * 0.5
        return round(min(1.0, max(0.0, raw)), 4)

    def _get_congress_reasoning(self, symbol: str) -> str | None:
        """Get enriched reasoning for recent congress trades on a symbol.

        Args:
            symbol: Ticker symbol.

        Returns:
            Reasoning string or None if no relevant trades.
        """
        thirty_days_ago = (
            datetime.now(UTC) - timedelta(days=30)
        ).strftime("%Y-%m-%d")

        rows = self.db.fetchall(
            """SELECT ct.*
               FROM congress_trades ct
               WHERE ct.symbol = ?
                 AND ct.date_traded >= ?
               ORDER BY ct.date_traded DESC
               LIMIT 3""",
            (symbol.upper(), thirty_days_ago),
        )

        if not rows:
            return None

        try:
            scorer = self.politician_scorer
            parts = []
            for row in rows:
                enriched = scorer.enrich_trade(dict(row))
                parts.append(scorer.build_reasoning(enriched))
            return " | ".join(parts)
        except Exception:
            return None

    def _check_price_triggers(
        self, symbol: str, thesis: Any,
    ) -> dict | None:
        """Check if price has hit significant movement thresholds.

        Args:
            symbol: Ticker symbol.
            thesis: Thesis model (for context).

        Returns:
            Dict with trigger info if significant move, None otherwise.
        """
        price_data = self._get_current_price_data(symbol)
        if not price_data:
            return None

        # Check daily movement trigger
        daily_trigger = self._check_daily_movement(price_data)
        if daily_trigger:
            return daily_trigger

        # Check weekly movement trigger
        weekly_trigger = self._check_weekly_movement(symbol, price_data)
        return weekly_trigger

    def _get_current_price_data(self, symbol: str) -> dict | None:
        """Get current price data, handling errors gracefully."""
        try:
            price_data = self.pricing.get_price(symbol, db=self.db)
        except Exception:
            logger.warning("signal_scan: price fetch failed for %s", symbol)
            return None

        if "error" in price_data or not price_data.get("price"):
            return None

        return price_data

    def _check_daily_movement(self, price_data: dict) -> dict | None:
        """Check if daily price movement exceeds threshold."""
        change_percent = price_data.get("change_percent")
        if change_percent is None:
            return None

        abs_change = abs(change_percent)
        if abs_change >= _DAILY_MOVE_THRESHOLD:
            return {
                "type": "daily_move",
                "price": price_data["price"],
                "change_percent": change_percent,
                "direction": "up" if change_percent > 0 else "down",
            }

        return None

    def _check_weekly_movement(self, symbol: str, price_data: dict) -> dict | None:
        """Check if weekly price movement exceeds threshold."""
        try:
            history = self.pricing.get_history(symbol, period="5d", db=self.db)
            if not history or len(history) < 2:
                return None

            start_price = history[0]["close"]
            end_price = history[-1]["close"]
            weekly_change = ((end_price - start_price) / start_price) * 100

            if abs(weekly_change) >= _WEEKLY_MOVE_THRESHOLD:
                return {
                    "type": "weekly_move",
                    "price": price_data["price"],
                    "change_percent": round(weekly_change, 2),
                    "direction": "up" if weekly_change > 0 else "down",
                }
        except Exception:
            pass

        return None

    def _generate_signal(
        self,
        action: str,
        symbol: str,
        thesis: Any,
        raw_confidence: float,
        reasoning: str,
    ) -> dict | None:
        """Create a signal after scoring and risk check.

        Args:
            action: "BUY" or "SELL".
            symbol: Ticker symbol.
            thesis: Thesis model.
            raw_confidence: Multi-factor confidence 0.0–1.0.
            reasoning: Human-readable reasoning string.

        Returns:
            Dict describing the created signal, or None if blocked.
        """
        confidence = self.signal_engine.score_confidence(
            raw_confidence=raw_confidence,
            thesis_status=thesis.status.value,
            source_type=SignalSource.THESIS_UPDATE.value,
        )

        if confidence < 0.3:
            logger.debug(
                "signal_scan: skipping %s %s — confidence %.2f too low",
                action, symbol, confidence,
            )
            return None

        nav = self.risk_manager._get_nav()
        size_pct = self._compute_position_size(
            symbol, confidence, nav,
        )

        signal_action = (
            SignalAction.BUY if action == "BUY" else SignalAction.SELL
        )

        signal = Signal(
            action=signal_action,
            symbol=symbol,
            thesis_id=thesis.id,
            confidence=round(confidence, 4),
            source=SignalSource.THESIS_UPDATE,
            horizon=thesis.horizon or "",
            reasoning=reasoning,
            size_pct=size_pct,
            status=SignalStatus.PENDING,
        )

        risk_result = self.risk_manager.pre_trade_check(signal)
        if not risk_result:
            logger.info(
                "signal_scan: %s %s blocked by risk: %s",
                action, symbol, risk_result.reason,
            )
            return None

        created = self.signal_engine.create_signal(signal)
        logger.info(
            "signal_scan: created %s signal for %s "
            "(confidence=%.2f, thesis=%d)",
            action, symbol, confidence, thesis.id,
        )
        return {
            "signal_id": created.id,
            "action": action,
            "symbol": symbol,
            "confidence": confidence,
            "size_pct": size_pct,
            "thesis_id": thesis.id,
            "reasoning": reasoning,
        }

    def _compute_position_size(
        self, symbol: str, confidence: float, nav: float,
    ) -> float:
        """Compute suggested position size as fraction of NAV.

        Base: 2% of NAV. Scale by confidence: size = base * confidence * 2.
        Cap at max_position_pct from risk limits.

        Args:
            symbol: Ticker symbol.
            confidence: Scored confidence 0.0–1.0.
            nav: Current portfolio NAV.

        Returns:
            Position size as fraction of NAV.
        """
        if nav <= 0:
            return _BASE_POSITION_SIZE

        size = _BASE_POSITION_SIZE * confidence * 2
        max_pct = self.risk_manager._get_limit(
            "max_position_pct", 0.15,
        )
        return min(size, max_pct)

    def _compute_raw_confidence(
        self, thesis: Any, trigger: dict | None,
    ) -> float:
        """Compute raw (pre-scoring) confidence based on thesis and trigger.

        Deprecated: use _compute_multi_factor_score instead. Kept for
        backward compatibility.

        Args:
            thesis: Thesis model.
            trigger: Price trigger dict or None.

        Returns:
            Raw confidence 0.0–1.0.
        """
        base = thesis.conviction if thesis.conviction else 0.5
        if trigger:
            abs_move = abs(trigger.get("change_percent", 0))
            trigger_boost = min(abs_move / 20.0, 0.15)
            base = min(base + trigger_boost, 1.0)
        return base

    def _build_reasoning(
        self,
        action: str,
        symbol: str,
        thesis: Any,
        trigger: dict | None,
        mf_score: MultiFactorScore | None = None,
    ) -> str:
        """Build human-readable reasoning for a signal.

        Args:
            action: "BUY" or "SELL".
            symbol: Ticker symbol.
            thesis: Thesis model.
            trigger: Price trigger dict or None.
            mf_score: Multi-factor score breakdown, if available.

        Returns:
            Reasoning string.
        """
        parts = []

        # Add thesis context
        parts.append(self._build_thesis_context(thesis))

        # Add action context
        parts.append(self._build_action_context(action, symbol, thesis))

        # Add price trigger info
        if trigger:
            parts.append(self._build_trigger_context(trigger))

        # Add multi-factor score details
        if mf_score:
            parts.extend(self._build_multifactor_context(mf_score))

        # Add congress details
        congress_detail = self._get_congress_reasoning(symbol)
        if congress_detail:
            parts.append(f"Congress: {congress_detail}")

        return ". ".join(parts) + "."

    def _build_thesis_context(self, thesis: Any) -> str:
        """Build thesis context part of reasoning."""
        return f"Thesis '{thesis.title}' ({thesis.status.value})"

    def _build_action_context(self, action: str, symbol: str, thesis: Any) -> str:
        """Build action context part of reasoning."""
        if action == "BUY":
            return f"{symbol} not yet in portfolio"
        else:
            return f"{symbol} held — thesis {thesis.status.value}"

    def _build_trigger_context(self, trigger: dict) -> str:
        """Build price trigger context part of reasoning."""
        return (
            f"Price {trigger['direction']} "
            f"{abs(trigger['change_percent']):.1f}% "
            f"({trigger['type']})"
        )

    def _build_multifactor_context(self, mf_score: MultiFactorScore) -> list[str]:
        """Build multi-factor score context parts of reasoning."""
        parts = []

        # Build factors list
        factors = []
        if mf_score.watchlist_trigger > 0:
            factors.append("watchlist trigger hit")
        if mf_score.news_sentiment > 0.6:
            factors.append("positive news sentiment")
        elif mf_score.news_sentiment < 0.4:
            factors.append("negative news sentiment")
        if mf_score.congress_alignment > 0.6:
            factors.append("congress buying aligned")

        if factors:
            parts.append("Factors: " + ", ".join(factors))

        parts.append(f"Multi-factor score: {mf_score.weighted_total:.2f}")
        return parts

    def _get_held_symbols(self) -> set[str]:
        """Get set of currently held symbols.

        Returns:
            Set of symbol strings with open positions.
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM positions WHERE shares > 0",
        )
        return {r["symbol"] for r in rows}

    def _get_pending_symbols(self) -> set[str]:
        """Get set of symbols with pending signals.

        Returns:
            Set of symbol strings with a pending signal.
        """
        rows = self.db.fetchall(
            "SELECT DISTINCT symbol FROM signals WHERE status = ?",
            (SignalStatus.PENDING.value,),
        )
        return {r["symbol"] for r in rows}
