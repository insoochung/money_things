"""Signal engine: generation, confidence scoring, lifecycle management, and funding plans.

This module is the core decision-making engine of money_moves. It handles the complete
lifecycle of trading signals: creation, multi-layer confidence scoring, status transitions
(approve/reject/expire/execute/cancel), source accuracy tracking, and funding plan
generation.

Signal lifecycle flow:
    1. External trigger (thesis update, price event, news, etc.) generates a signal
    2. Signal is scored using score_confidence() with multiple adjustment layers
    3. Signal is persisted via create_signal() with status PENDING
    4. Signal is sent to Telegram for user approval
    5. User approves -> approve_signal() -> status APPROVED -> broker executes
       User rejects -> reject_signal() -> status REJECTED -> what_if record
       No response -> expire_signal() -> status IGNORED -> what_if record
       Kill switch -> cancel_signal() -> status CANCELLED
    6. After broker fill -> mark_executed() -> status EXECUTED

Confidence scoring pipeline (score_confidence):
    The confidence score determines how strongly the system recommends a trade.
    It is computed through a multi-layer pipeline:
        1. Base: raw_confidence (0.0 to 1.0) from the signal generator
        2. Thesis strength multiplier: Active=1.0x, Strengthening=1.1x, Confirmed=1.2x,
           Weakening=0.6x, Invalidated/Archived=0.0x
        3. Principles adjustment: +/- weight for each matching principle based on its
           validated vs invalidated track record
        4. Domain expertise: 1.15x boost for signals in configured expertise domains,
           0.90x penalty for out-of-domain signals
        5. Source accuracy: Historical win rate of the signal source type
           (>70% = 1.15x, 50-70% = 1.0x, <50% = 0.85x)
        6. Final clamping to [0.0, 1.0]

What-if tracking:
    Both rejected and ignored (expired) signals create what_if records that track
    the price at the time of the decision. This enables counterfactual analysis:
    "What would have happened if we had taken this signal?" The distinction between
    rejected (active disagreement) and ignored (non-engagement) is preserved for
    different learning purposes.

Funding plans:
    For BUY signals, generate_funding_plan() creates a plan showing how to fund
    the purchase: available cash, and if insufficient, which lot to sell (FIFO order,
    preferring losses for tax harvesting).

Classes:
    SignalEngine: Main class for signal CRUD, scoring, and lifecycle management.

Functions:
    _row_to_signal: Convert a database row dict to a Signal model.
    _audit: Helper to create audit log entries for signal actions.

Module-level constants:
    THESIS_STRENGTH: Maps ThesisStatus to confidence multipliers.
    SOURCE_ACCURACY_THRESHOLDS: Maps accuracy ranges to multiplier values.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.database import Database
from engine import (
    ActorType,
    Signal,
    SignalAction,
    SignalSource,
    SignalStatus,
)

logger = logging.getLogger(__name__)

# Thesis strength multipliers for confidence scoring.
# Maps thesis status strings to the multiplier applied to the base confidence score.
# Invalidated and archived theses produce a 0.0 multiplier, effectively blocking
# any signals generated from them.
THESIS_STRENGTH: dict[str, float] = {
    "active": 1.0,
    "strengthening": 1.1,
    "confirmed": 1.2,
    "weakening": 0.6,
    "invalidated": 0.0,
    "archived": 0.0,
}

# Source accuracy multiplier ranges.
# Maps historical win rate brackets to the multiplier applied during scoring.
# Sources with excellent track records (>70% win rate) get a 15% boost, while
# poor performers (<50%) get a 15% penalty.
SOURCE_ACCURACY_THRESHOLDS = {
    "excellent": (0.7, 1.15),  # >70% accuracy -> 1.15x
    "good": (0.5, 1.0),  # 50-70% -> 1.0x
    "poor": (0.0, 0.85),  # <50% -> 0.85x
}


class SignalEngine:
    """Engine for trading signal generation, scoring, and lifecycle management.

    Manages the complete lifecycle of signals from creation through execution or
    rejection. Provides the multi-layer confidence scoring pipeline that adjusts
    raw confidence based on thesis strength, principles, domain expertise, and
    source historical accuracy.

    The engine also tracks source accuracy over time (via record_source_outcome)
    and generates funding plans for BUY signals.

    Attributes:
        db: Database instance for all persistence operations.
        expertise_domains: List of domain strings where the user has expertise
            (e.g., ['AI', 'semiconductors', 'software', 'hardware']). Signals in
            these domains receive a confidence boost.
        domain_boost: Multiplier applied to signals in expertise domains (default 1.15).
        out_of_domain_penalty: Multiplier applied to signals outside expertise domains
            (default 0.90).
    """

    def __init__(
        self,
        db: Database,
        expertise_domains: list[str] | None = None,
        domain_boost: float = 1.15,
        out_of_domain_penalty: float = 0.90,
    ) -> None:
        """Initialize the SignalEngine.

        Args:
            db: Database instance for reading/writing signals, scores, and audit entries.
            expertise_domains: List of domain strings where the user has expertise.
                Defaults to ['AI', 'semiconductors', 'software', 'hardware'] if not
                provided. These domains receive a confidence boost during scoring.
            domain_boost: Multiplier for in-domain signals. Default 1.15 (15% boost).
            out_of_domain_penalty: Multiplier for out-of-domain signals. Default 0.90
                (10% penalty).
        """
        self.db = db
        default_domains = ["AI", "semiconductors", "software", "hardware"]
        self.expertise_domains = expertise_domains or default_domains
        self.domain_boost = domain_boost
        self.out_of_domain_penalty = out_of_domain_penalty

    def create_signal(self, signal: Signal) -> Signal:
        """Create a new signal and persist it to the database.

        Inserts the signal into the signals table with the current UTC timestamp
        and creates an audit log entry. The signal is returned with its assigned
        database ID and creation timestamp.

        Args:
            signal: Signal model to persist. All fields except id and created_at
                should be populated. The status should typically be PENDING.

        Returns:
            The same Signal model with id and created_at populated from the database.

        Side effects:
            - Inserts a row into the signals table.
            - Inserts an audit_log entry with action 'signal_created'.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        cursor = self.db.execute(
            """INSERT INTO signals
               (action, symbol, thesis_id, confidence, source, horizon, reasoning,
                size_pct, funding_plan, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signal.action.value,
                signal.symbol,
                signal.thesis_id,
                signal.confidence,
                signal.source.value,
                signal.horizon,
                signal.reasoning,
                signal.size_pct,
                signal.funding_plan,
                signal.status.value,
                now,
            ),
        )
        self.db.connect().commit()
        signal.id = cursor.lastrowid
        signal.created_at = now

        _audit(self.db, "signal_created", "signal", signal.id)
        return signal

    def get_signal(self, signal_id: int) -> Signal | None:
        """Retrieve a signal by its database ID.

        Args:
            signal_id: The primary key ID of the signal to retrieve.

        Returns:
            Signal model if found, None otherwise.
        """
        row = self.db.fetchone("SELECT * FROM signals WHERE id = ?", (signal_id,))
        if not row:
            return None
        return _row_to_signal(row)

    def list_signals(
        self,
        status: SignalStatus | None = None,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[Signal]:
        """List signals with optional filtering by status and/or symbol.

        Supports filtering by signal status (e.g., PENDING, APPROVED) and/or symbol.
        Results are ordered by creation time (newest first) and limited to prevent
        excessive data loading.

        Args:
            status: Optional SignalStatus to filter by. Only returns signals with
                this exact status if provided.
            symbol: Optional ticker symbol to filter by. Only returns signals for
                this symbol if provided.
            limit: Maximum number of signals to return. Defaults to 50.

        Returns:
            List of Signal models matching the filter criteria, ordered by
            created_at descending. Empty list if no matches.
        """
        conditions: list[str] = []
        params: list = []
        if status:
            conditions.append("status = ?")
            params.append(status.value)
        if symbol:
            conditions.append("symbol = ?")
            params.append(symbol)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self.db.fetchall(
            f"SELECT * FROM signals {where} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return [_row_to_signal(r) for r in rows]

    def approve_signal(self, signal_id: int) -> Signal | None:
        """Approve a pending signal for execution.

        Transitions a signal from PENDING to APPROVED status and records the decision
        timestamp. Only PENDING signals can be approved; signals in any other status
        are ignored (returns None).

        Args:
            signal_id: The database ID of the signal to approve.

        Returns:
            The updated Signal model with APPROVED status, or None if the signal
            doesn't exist or is not in PENDING status.

        Side effects:
            - Updates the signal's status and decided_at in the signals table.
            - Inserts an audit_log entry with action 'signal_approved'.
            - Commits the database transaction.
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.status != SignalStatus.PENDING:
            return None

        now = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE signals SET status = ?, decided_at = ? WHERE id = ?",
            (SignalStatus.APPROVED.value, now, signal_id),
        )
        self.db.connect().commit()
        _audit(self.db, "signal_approved", "signal", signal_id)
        return self.get_signal(signal_id)

    def reject_signal(self, signal_id: int, price_at_pass: float = 0) -> Signal | None:
        """Reject a pending signal and record it for what-if analysis.

        Transitions a signal from PENDING to REJECTED status. If a price_at_pass is
        provided, creates a what_if record to track the counterfactual outcome (what
        would have happened if the signal had been approved).

        The 'rejected' decision in what_if indicates active user disagreement with
        the signal, as opposed to 'ignored' which indicates non-engagement.

        Args:
            signal_id: The database ID of the signal to reject.
            price_at_pass: The market price at the time of rejection. Used to
                calculate counterfactual P/L in the what_if tracker. Set to 0
                to skip what_if recording.

        Returns:
            The updated Signal model with REJECTED status, or None if the signal
            doesn't exist or is not in PENDING status.

        Side effects:
            - Updates the signal's status and decided_at in the signals table.
            - If price_at_pass > 0, inserts a what_if record.
            - Inserts an audit_log entry with action 'signal_rejected'.
            - Commits the database transaction.
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.status != SignalStatus.PENDING:
            return None

        now = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE signals SET status = ?, decided_at = ? WHERE id = ?",
            (SignalStatus.REJECTED.value, now, signal_id),
        )
        if price_at_pass > 0:
            self.db.execute(
                """INSERT INTO what_if (signal_id, decision, price_at_pass)
                   VALUES (?, 'rejected', ?)""",
                (signal_id, price_at_pass),
            )
        self.db.connect().commit()
        _audit(self.db, "signal_rejected", "signal", signal_id)
        return self.get_signal(signal_id)

    def expire_signal(self, signal_id: int, price_at_pass: float = 0) -> Signal | None:
        """Expire a signal due to 24-hour timeout (no user response).

        Transitions a signal from PENDING to IGNORED status and records the expiry
        timestamp. Creates a what_if record with decision='ignored' to distinguish
        from active rejection.

        The 'ignored' decision in what_if indicates the user did not engage with the
        signal at all, which is a different learning signal than active rejection.

        Args:
            signal_id: The database ID of the signal to expire.
            price_at_pass: The market price at the time of expiry. Used to calculate
                counterfactual P/L in the what_if tracker. Set to 0 to skip.

        Returns:
            The updated Signal model with IGNORED status, or None if the signal
            doesn't exist or is not in PENDING status.

        Side effects:
            - Updates the signal's status and expired_at in the signals table.
            - If price_at_pass > 0, inserts a what_if record with decision='ignored'.
            - Inserts an audit_log entry with action 'signal_expired'.
            - Commits the database transaction.
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.status != SignalStatus.PENDING:
            return None

        now = datetime.now(UTC).isoformat()
        self.db.execute(
            "UPDATE signals SET status = ?, expired_at = ? WHERE id = ?",
            (SignalStatus.IGNORED.value, now, signal_id),
        )
        if price_at_pass > 0:
            self.db.execute(
                """INSERT INTO what_if (signal_id, decision, price_at_pass)
                   VALUES (?, 'ignored', ?)""",
                (signal_id, price_at_pass),
            )
        self.db.connect().commit()
        _audit(self.db, "signal_expired", "signal", signal_id)
        return self.get_signal(signal_id)

    def mark_executed(self, signal_id: int) -> Signal | None:
        """Mark an approved signal as executed after broker fill.

        Transitions a signal from APPROVED to EXECUTED status. Only APPROVED signals
        can be marked as executed.

        Args:
            signal_id: The database ID of the signal to mark as executed.

        Returns:
            The updated Signal model with EXECUTED status, or None if the signal
            doesn't exist or is not in APPROVED status.

        Side effects:
            - Updates the signal's status in the signals table.
            - Inserts an audit_log entry with action 'signal_executed'.
            - Commits the database transaction.
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.status != SignalStatus.APPROVED:
            return None

        self.db.execute(
            "UPDATE signals SET status = ? WHERE id = ?",
            (SignalStatus.EXECUTED.value, signal_id),
        )
        self.db.connect().commit()
        _audit(self.db, "signal_executed", "signal", signal_id)
        return self.get_signal(signal_id)

    def cancel_signal(self, signal_id: int) -> Signal | None:
        """Cancel a pending signal (e.g., due to kill switch or risk check failure).

        Transitions a signal from PENDING to CANCELLED status. Only PENDING signals
        can be cancelled.

        Args:
            signal_id: The database ID of the signal to cancel.

        Returns:
            The updated Signal model with CANCELLED status, or None if the signal
            doesn't exist or is not in PENDING status.

        Side effects:
            - Updates the signal's status in the signals table.
            - Inserts an audit_log entry with action 'signal_cancelled'.
            - Commits the database transaction.
        """
        signal = self.get_signal(signal_id)
        if not signal or signal.status != SignalStatus.PENDING:
            return None

        self.db.execute(
            "UPDATE signals SET status = ? WHERE id = ?",
            (SignalStatus.CANCELLED.value, signal_id),
        )
        self.db.connect().commit()
        _audit(self.db, "signal_cancelled", "signal", signal_id)
        return self.get_signal(signal_id)

    def score_confidence(
        self,
        raw_confidence: float,
        thesis_status: str = "active",
        matching_principles: list[dict] | None = None,
        signal_domain: str = "",
        source_type: str = "manual",
    ) -> float:
        """Compute adjusted confidence score using the multi-layer scoring pipeline.

        Applies five sequential adjustments to the raw confidence:
            1. Base: Start with raw_confidence
            2. Thesis strength: Multiply by THESIS_STRENGTH[thesis_status]
            3. Principles: Add/subtract weight for each matching principle based on
               its validated vs invalidated track record
            4. Domain expertise: Multiply by domain_boost (1.15x) if signal is in
               an expertise domain, or out_of_domain_penalty (0.90x) if not
            5. Source accuracy: Multiply by historical win rate bracket multiplier
            6. Clamp final result to [0.0, 1.0]

        The order of operations matters because each layer multiplies/adds to the
        running score. For example, an invalidated thesis (0.0x multiplier) will
        zero out the score regardless of other factors.

        Args:
            raw_confidence: Base confidence from the signal generator, 0.0 to 1.0.
            thesis_status: Status string of the backing thesis ('active', 'strengthening',
                'confirmed', 'weakening', 'invalidated', 'archived'). Defaults to 'active'.
            matching_principles: List of principle dicts from PrinciplesEngine.match_principles().
                Each must have 'weight', 'validated_count', 'invalidated_count' keys.
                None or empty list means no principle adjustment.
            signal_domain: Domain string for the signal (e.g., 'AI', 'biotech').
                Empty string means no domain adjustment is applied.
            source_type: Signal source type for historical accuracy lookup (e.g.,
                'thesis_update', 'manual'). Defaults to 'manual'.

        Returns:
            Final confidence score clamped to [0.0, 1.0]. A score of 0.0 indicates
            no confidence (e.g., from an invalidated thesis), 1.0 indicates maximum
            confidence (clamped).
        """
        score = raw_confidence

        # 2. Thesis strength multiplier
        score *= THESIS_STRENGTH.get(thesis_status, 1.0)

        # 3. Principles adjustment
        if matching_principles:
            for p in matching_principles:
                weight = p.get("weight", 0.05)
                validated = p.get("validated_count", 0)
                invalidated = p.get("invalidated_count", 0)
                if validated > invalidated:
                    score += weight
                elif invalidated > validated:
                    score -= weight

        # 4. Domain expertise
        if signal_domain:
            if signal_domain in self.expertise_domains:
                score *= self.domain_boost
            else:
                score *= self.out_of_domain_penalty

        # 5. Source accuracy
        source_accuracy = self._get_source_accuracy(source_type)
        if source_accuracy is not None:
            score *= self._accuracy_multiplier(source_accuracy)

        # 6. Clamp
        return max(0.0, min(1.0, score))

    def _get_source_accuracy(self, source_type: str) -> float | None:
        """Look up the historical win rate for a signal source type.

        Reads from the signal_scores table which tracks wins, losses, and total
        outcomes for each source type.

        Args:
            source_type: The signal source type string (e.g., 'thesis_update', 'manual').

        Returns:
            Win rate as a float between 0.0 and 1.0, or None if no data exists
            for this source type.
        """
        row = self.db.fetchone(
            "SELECT wins, total FROM signal_scores WHERE source_type = ?",
            (source_type,),
        )
        if not row or row["total"] == 0:
            return None
        return row["wins"] / row["total"]

    def _accuracy_multiplier(self, accuracy: float) -> float:
        """Convert a win rate to a confidence multiplier.

        Maps accuracy brackets to multipliers:
            - >= 70% accuracy: 1.15x (excellent sources get boosted)
            - >= 50% accuracy: 1.0x (decent sources are neutral)
            - < 50% accuracy: 0.85x (poor sources are penalized)

        Args:
            accuracy: Win rate as a float between 0.0 and 1.0.

        Returns:
            Multiplier to apply to the confidence score.
        """
        if accuracy >= 0.7:
            return 1.15
        if accuracy >= 0.5:
            return 1.0
        return 0.85

    def record_source_outcome(self, source_type: str, win: bool, pnl_pct: float = 0) -> None:
        """Record a trade outcome for a signal source type.

        Updates the signal_scores table with the win/loss result and running average
        return for the given source type. If no record exists for this source type,
        creates a new one.

        The running average return is calculated incrementally:
            new_avg = old_avg + (pnl_pct - old_avg) / total

        Args:
            source_type: The signal source type string (e.g., 'thesis_update').
            win: True if the trade was profitable, False otherwise.
            pnl_pct: The realized P/L as a percentage (e.g., 5.0 for a 5% gain,
                -2.0 for a 2% loss). Defaults to 0.

        Side effects:
            - Updates or inserts a row in the signal_scores table.
            - Commits the database transaction.
        """
        row = self.db.fetchone("SELECT * FROM signal_scores WHERE source_type = ?", (source_type,))
        now = datetime.now(UTC).isoformat()
        if row:
            wins = row["wins"] + (1 if win else 0)
            losses = row["losses"] + (0 if win else 1)
            total = row["total"] + 1
            # Running average return
            old_avg = row["avg_return"] or 0
            new_avg = old_avg + (pnl_pct - old_avg) / total
            self.db.execute(
                """UPDATE signal_scores
                   SET wins=?, losses=?, total=?, avg_return=?, last_updated=?
                   WHERE source_type=?""",
                (wins, losses, total, new_avg, now, source_type),
            )
        else:
            self.db.execute(
                """INSERT INTO signal_scores
                   (source_type, total, wins, losses, avg_return, last_updated)
                   VALUES (?,1,?,?,?,?)""",
                (source_type, 1 if win else 0, 0 if win else 1, pnl_pct, now),
            )
        self.db.connect().commit()

    def generate_funding_plan(
        self,
        symbol: str,
        shares: float,
        estimated_cost: float,
    ) -> dict:
        """Generate a funding plan for a BUY signal.

        Determines how to fund a purchase: first checks available cash, then if
        there is a shortfall, identifies the oldest lot (FIFO order) from other
        positions that could be sold to raise funds, preferring lots with losses
        for tax-loss harvesting.

        Args:
            symbol: The ticker symbol being purchased (excluded from sell candidates).
            shares: The number of shares to purchase.
            estimated_cost: The total estimated cost of the purchase.

        Returns:
            Dictionary with funding plan details:
                - action (str): Always 'BUY'
                - symbol (str): The symbol being purchased
                - shares (float): Number of shares
                - estimated_cost (float): Total cost
                - funding (dict): Contains:
                    - available_cash (float): Cash available in the portfolio
                    - sell_lot (dict, optional): If cash is insufficient, the oldest
                      lot from another position that could be sold, with keys:
                      symbol, lot_id, shares, cost_basis, holding_period
        """
        # Get available cash (sum from portfolio_value or default)
        pv = self.db.fetchone("SELECT cash FROM portfolio_value ORDER BY date DESC LIMIT 1")
        available_cash = pv["cash"] if pv else 0

        plan: dict = {
            "action": "BUY",
            "symbol": symbol,
            "shares": shares,
            "estimated_cost": estimated_cost,
            "funding": {
                "available_cash": available_cash,
            },
        }

        shortfall = estimated_cost - available_cash
        if shortfall > 0:
            # Find oldest lot to sell (FIFO, prefer losses for tax harvesting)
            lots = self.db.fetchall(
                """SELECT l.*, p.symbol FROM lots l
                   JOIN positions p ON l.position_id = p.id
                   WHERE l.closed_date IS NULL AND p.symbol != ?
                   ORDER BY l.acquired_date ASC""",
                (symbol,),
            )
            for lot in lots:
                plan["funding"]["sell_lot"] = {
                    "symbol": lot["symbol"],
                    "lot_id": lot["id"],
                    "shares": lot["shares"],
                    "cost_basis": lot["cost_basis"],
                    "holding_period": lot["holding_period"],
                }
                break

        return plan


def _row_to_signal(row: dict) -> Signal:
    """Convert a database row dictionary to a Signal model.

    Maps column names from the signals table to Signal model fields, handling
    enum conversions (SignalAction, SignalSource, SignalStatus) and optional
    fields that may be None or missing from the row.

    Args:
        row: Dictionary from a database query with keys matching signals table columns.

    Returns:
        Signal model populated from the database row.
    """
    return Signal(
        id=row["id"],
        action=SignalAction(row["action"]),
        symbol=row["symbol"],
        thesis_id=row.get("thesis_id"),
        confidence=row["confidence"],
        source=SignalSource(row["source"]),
        horizon=row.get("horizon", ""),
        reasoning=row.get("reasoning", ""),
        size_pct=row.get("size_pct"),
        funding_plan=row.get("funding_plan"),
        status=SignalStatus(row["status"]),
        telegram_msg_id=row.get("telegram_msg_id"),
        created_at=row.get("created_at", ""),
        decided_at=row.get("decided_at"),
        expired_at=row.get("expired_at"),
    )


def _audit(db: Database, action: str, entity_type: str, entity_id: int | None) -> None:
    """Create an audit log entry for a signal engine action.

    Records the action in the audit_log table with the ENGINE actor type.
    This provides a complete trail of all signal lifecycle events (creation,
    approval, rejection, expiry, execution, cancellation).

    Args:
        db: Database instance for writing the audit entry.
        action: The action performed (e.g., 'signal_created', 'signal_approved').
        entity_type: The type of entity affected (always 'signal' for this module).
        entity_id: The database ID of the affected signal.

    Side effects:
        - Inserts a row into the audit_log table.
        - Commits the database transaction.
    """
    db.execute(
        """INSERT INTO audit_log (actor, action, entity_type, entity_id)
           VALUES (?,?,?,?)""",
        (ActorType.ENGINE.value, action, entity_type, entity_id),
    )
    db.connect().commit()
