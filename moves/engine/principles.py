"""Principles engine: self-learning investment rules that evolve with trade outcomes.

This module implements the principles system for money_moves -- a set of investment
rules that are validated or invalidated based on actual trade outcomes. Principles
represent distilled investment wisdom (e.g., 'Domain expertise creates durable edge')
that influence signal confidence scoring and trading decisions.

The principles engine is a key part of the feedback loop between money_thoughts and
money_moves. As trades are executed and outcomes observed, principles are validated
(positive outcome) or invalidated (negative outcome). Over time, principles that
consistently lead to poor outcomes are automatically deactivated, while validated
principles gain more influence in confidence scoring.

How principles fit into the system:
    1. Principles are created from user input or imported from money_journal
    2. When a signal is generated, matching principles are found via match_principles()
    3. Matching principles adjust the signal's confidence score via apply_to_score()
    4. After trade execution, outcomes validate or invalidate the principles used
    5. Poorly performing principles are automatically deactivated

The principles table schema includes:
    - text: The principle statement
    - category: Classification ('domain', 'conviction', 'risk', etc.)
    - origin: Where the principle came from ('user_input', 'journal_import', 'learned')
    - weight: How much this principle adjusts confidence (default 0.05 = 5%)
    - validated_count: Number of trades where this principle led to profit
    - invalidated_count: Number of trades where this principle led to loss
    - active: Whether the principle is currently in use
    - last_applied: Timestamp of when the principle was last used in scoring

Classes:
    PrinciplesEngine: Main class for CRUD operations, matching, and scoring.

Functions:
    _audit: Helper to create audit log entries for principle actions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.database import Database
from engine import ActorType

logger = logging.getLogger(__name__)


class PrinciplesEngine:
    """Engine for managing self-learning investment principles.

    Provides CRUD operations for principles, matching logic to find relevant
    principles for a given signal context, and scoring logic to convert matched
    principles into confidence adjustments. Also implements the self-learning
    feedback loop through validate/invalidate/deactivate methods.

    The engine operates on the 'principles' table in the SQLite database.

    Attributes:
        db: Database instance used for all persistence operations.
    """

    def __init__(self, db: Database) -> None:
        """Initialize the PrinciplesEngine with a database connection.

        Args:
            db: Database instance for reading/writing principles and audit entries.
        """
        self.db = db

    def get_all(self, active_only: bool = True) -> list[dict]:
        """Retrieve all principles, optionally filtered to active ones only.

        Args:
            active_only: If True (default), only return principles where active=TRUE.
                If False, return all principles including deactivated ones.

        Returns:
            List of principle dictionaries with all columns from the principles table,
            including: id, text, category, origin, weight, validated_count,
            invalidated_count, active, last_applied, created_at.
            Ordered by id (creation order).
        """
        if active_only:
            return self.db.fetchall("SELECT * FROM principles WHERE active = TRUE ORDER BY id")
        return self.db.fetchall("SELECT * FROM principles ORDER BY id")

    def get_principle(self, principle_id: int) -> dict | None:
        """Retrieve a single principle by its database ID.

        Args:
            principle_id: The primary key ID of the principle to retrieve.

        Returns:
            Dictionary with all principle columns, or None if not found.
        """
        return self.db.fetchone("SELECT * FROM principles WHERE id = ?", (principle_id,))

    def create_principle(
        self,
        text: str,
        category: str = "",
        origin: str = "user_input",
        weight: float = 0.05,
    ) -> int:
        """Create a new investment principle.

        Creates a new principle record in the database and logs the creation
        in the audit trail.

        Args:
            text: The principle statement (e.g., 'Domain expertise creates durable edge').
            category: Classification for matching logic. Common values:
                'domain' (matched when signal has a domain context),
                'conviction' (always matched),
                'risk' (always matched for risk-related signals).
            origin: Where the principle came from. Common values:
                'user_input', 'journal_import', 'learned'.
            weight: How much this principle adjusts confidence score. Default 0.05
                means a validated principle adds 5% to confidence, an invalidated
                one subtracts 5%.

        Returns:
            The database ID of the newly created principle.

        Side effects:
            - Inserts a row into the principles table.
            - Inserts an audit_log entry with action 'principle_created'.
            - Commits the database transaction.
        """
        cursor = self.db.execute(
            """INSERT INTO principles (text, category, origin, weight)
               VALUES (?,?,?,?)""",
            (text, category, origin, weight),
        )
        self.db.connect().commit()
        pid = cursor.lastrowid
        _audit(self.db, "principle_created", "principle", pid)
        return pid

    def match_principles(self, signal_context: dict) -> list[dict]:
        """Find principles that are relevant to a given signal context.

        Uses category-based matching to determine which principles should
        influence the confidence scoring for a specific signal. The matching
        logic is designed to be simple and deterministic:

        - 'domain' category principles: Match when the signal has a domain context
          AND the principle text contains domain-related keywords (domain, expertise,
          legacy, tech).
        - 'conviction' category principles: Always match (universal rules about
          conviction levels).
        - 'risk' category principles: Always match (risk rules apply universally).

        Args:
            signal_context: Dictionary describing the signal being scored. Expected keys:
                - domain (str): The signal's domain (e.g., 'AI', 'biotech'). Used for
                  matching domain-category principles.
                - symbol (str): The ticker symbol (not currently used in matching).
                - action (str): The signal action (not currently used in matching).
                - source (str): The signal source (not currently used in matching).

        Returns:
            List of matching principle dictionaries (same format as get_all()).
            May contain duplicates if a principle matches multiple criteria, though
            the current logic uses ``continue`` to prevent this.
        """
        principles = self.get_all(active_only=True)
        matched = []

        domain = signal_context.get("domain", "").lower()

        for p in principles:
            text_lower = p["text"].lower()
            category = p.get("category", "")

            # Domain-related principles match domain signals
            if category == "domain" and domain:
                if any(kw in text_lower for kw in ["domain", "expertise", "legacy", "tech"]):
                    matched.append(p)
                    continue

            # Conviction principles always apply
            if category == "conviction":
                matched.append(p)
                continue

            # Risk principles match risky situations
            if category == "risk":
                matched.append(p)
                continue

        return matched

    def validate_principle(self, principle_id: int) -> None:
        """Record a positive outcome for a principle (trade was profitable).

        Called after a trade that used this principle in its confidence scoring
        results in a profit. Increments the validated_count and updates last_applied
        timestamp.

        Args:
            principle_id: The database ID of the principle to validate.

        Side effects:
            - Updates validated_count and last_applied in the principles table.
            - Inserts an audit_log entry with action 'principle_validated'.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """UPDATE principles
               SET validated_count = validated_count + 1, last_applied = ?
               WHERE id = ?""",
            (now, principle_id),
        )
        self.db.connect().commit()
        _audit(self.db, "principle_validated", "principle", principle_id)

    def invalidate_principle(self, principle_id: int) -> None:
        """Record a negative outcome for a principle (trade was unprofitable).

        Called after a trade that used this principle in its confidence scoring
        results in a loss. Increments the invalidated_count and updates last_applied
        timestamp.

        Args:
            principle_id: The database ID of the principle to invalidate.

        Side effects:
            - Updates invalidated_count and last_applied in the principles table.
            - Inserts an audit_log entry with action 'principle_invalidated'.
            - Commits the database transaction.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """UPDATE principles
               SET invalidated_count = invalidated_count + 1, last_applied = ?
               WHERE id = ?""",
            (now, principle_id),
        )
        self.db.connect().commit()
        _audit(self.db, "principle_invalidated", "principle", principle_id)

    def deactivate_if_poor(self, principle_id: int) -> bool:
        """Deactivate a principle if its track record is consistently poor.

        A principle is deactivated when its invalidated_count exceeds twice its
        validated_count AND the invalidated_count is greater than 2 (to avoid
        deactivating principles with too few data points).

        This is the self-learning mechanism: principles that consistently lead
        to losses are automatically retired from the scoring system.

        Args:
            principle_id: The database ID of the principle to evaluate.

        Returns:
            True if the principle was deactivated, False if it was not (either
            because the principle doesn't exist or its performance doesn't meet
            the deactivation threshold).

        Side effects:
            - If deactivated: sets active=FALSE in the principles table.
            - If deactivated: inserts audit_log entry with action 'principle_deactivated'.
            - Commits the database transaction if deactivated.
        """
        p = self.get_principle(principle_id)
        if not p:
            return False
        if p["invalidated_count"] > p["validated_count"] * 2 and p["invalidated_count"] > 2:
            self.db.execute(
                "UPDATE principles SET active = FALSE WHERE id = ?",
                (principle_id,),
            )
            self.db.connect().commit()
            _audit(self.db, "principle_deactivated", "principle", principle_id)
            return True
        return False

    def apply_to_score(self, matching_principles: list[dict]) -> float:
        """Calculate total confidence score adjustment from a list of matching principles.

        For each principle, its weight is added (if validated > invalidated) or
        subtracted (if invalidated > validated) from a running adjustment total.
        Principles with equal validated and invalidated counts contribute nothing.

        As a side effect, each principle's last_applied timestamp is updated to
        the current time, enabling tracking of principle freshness.

        Args:
            matching_principles: List of principle dictionaries (from match_principles()
                or get_all()). Each must have keys: 'id', 'weight', 'validated_count',
                'invalidated_count'.

        Returns:
            Float representing the total confidence adjustment. Positive values
            boost confidence (more validated principles), negative values reduce it
            (more invalidated principles). Typical range: -0.15 to +0.15 depending
            on the number and weights of matching principles.

        Side effects:
            - Updates last_applied for all provided principles.
            - Commits the database transaction.
        """
        adjustment = 0.0
        now = datetime.now(UTC).isoformat()
        for p in matching_principles:
            weight = p.get("weight", 0.05)
            v = p.get("validated_count", 0)
            iv = p.get("invalidated_count", 0)
            if v > iv:
                adjustment += weight
            elif iv > v:
                adjustment -= weight
            # Mark as applied
            self.db.execute(
                "UPDATE principles SET last_applied = ? WHERE id = ?",
                (now, p["id"]),
            )
        self.db.connect().commit()
        return adjustment


def _audit(db: Database, action: str, entity_type: str, entity_id: int | None) -> None:
    """Create an audit log entry for a principles engine action.

    Records the action in the audit_log table with the ENGINE actor type.
    This provides a complete trail of all principle lifecycle events
    (creation, validation, invalidation, deactivation).

    Args:
        db: Database instance for writing the audit entry.
        action: The action performed (e.g., 'principle_created', 'principle_validated').
        entity_type: The type of entity affected (always 'principle' for this module).
        entity_id: The database ID of the affected principle.

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
