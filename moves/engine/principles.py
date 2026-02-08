"""Principles engine: self-learning investment rules that evolve with trade outcomes.

All methods now accept user_id for multi-user scoping.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from db.database import Database
from engine import ActorType

logger = logging.getLogger(__name__)


class PrinciplesEngine:
    """Engine for managing self-learning investment principles."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def get_all(self, user_id: int, active_only: bool = True) -> list[dict]:
        """Retrieve all principles for a user.

        Args:
            user_id: ID of the owning user.
            active_only: If True, only return active principles.

        Returns:
            List of principle dictionaries.
        """
        if active_only:
            return self.db.fetchall(
                "SELECT * FROM principles WHERE user_id = ? AND active = TRUE ORDER BY id",
                (user_id,),
            )
        return self.db.fetchall(
            "SELECT * FROM principles WHERE user_id = ? ORDER BY id",
            (user_id,),
        )

    def get_principle(self, principle_id: int, user_id: int) -> dict | None:
        """Retrieve a single principle by its database ID.

        Args:
            principle_id: The primary key ID.
            user_id: ID of the owning user.

        Returns:
            Dictionary with all principle columns, or None.
        """
        return self.db.fetchone(
            "SELECT * FROM principles WHERE id = ? AND user_id = ?",
            (principle_id, user_id),
        )

    def create_principle(
        self,
        text: str,
        category: str = "",
        origin: str = "user_input",
        weight: float = 0.05,
        user_id: int = 0,
    ) -> int:
        """Create a new investment principle.

        Args:
            text: The principle statement.
            category: Classification for matching logic.
            origin: Where the principle came from.
            weight: How much this principle adjusts confidence score.
            user_id: ID of the owning user.

        Returns:
            The database ID of the newly created principle.
        """
        cursor = self.db.execute(
            """INSERT INTO principles (text, category, origin, weight, user_id)
               VALUES (?,?,?,?,?)""",
            (text, category, origin, weight, user_id),
        )
        self.db.connect().commit()
        pid = cursor.lastrowid
        _audit(self.db, "principle_created", "principle", pid)
        return pid

    def match_principles(self, signal_context: dict, user_id: int) -> list[dict]:
        """Find principles relevant to a given signal context.

        Args:
            signal_context: Dictionary describing the signal being scored.
            user_id: ID of the owning user.

        Returns:
            List of matching principle dictionaries.
        """
        principles = self.get_all(user_id, active_only=True)
        matched = []

        domain = signal_context.get("domain", "").lower()

        for p in principles:
            text_lower = p["text"].lower()
            category = p.get("category", "")

            if category == "domain" and domain:
                if any(kw in text_lower for kw in ["domain", "expertise", "legacy", "tech"]):
                    matched.append(p)
                    continue

            if category == "conviction":
                matched.append(p)
                continue

            if category == "risk":
                matched.append(p)
                continue

        return matched

    def validate_principle(self, principle_id: int, user_id: int) -> None:
        """Record a positive outcome for a principle.

        Args:
            principle_id: The database ID of the principle.
            user_id: ID of the owning user.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """UPDATE principles
               SET validated_count = validated_count + 1, last_applied = ?
               WHERE id = ? AND user_id = ?""",
            (now, principle_id, user_id),
        )
        self.db.connect().commit()
        _audit(self.db, "principle_validated", "principle", principle_id)

    def invalidate_principle(self, principle_id: int, user_id: int) -> None:
        """Record a negative outcome for a principle.

        Args:
            principle_id: The database ID of the principle.
            user_id: ID of the owning user.
        """
        now = datetime.now(UTC).isoformat()
        self.db.execute(
            """UPDATE principles
               SET invalidated_count = invalidated_count + 1, last_applied = ?
               WHERE id = ? AND user_id = ?""",
            (now, principle_id, user_id),
        )
        self.db.connect().commit()
        _audit(self.db, "principle_invalidated", "principle", principle_id)

    def deactivate_if_poor(self, principle_id: int, user_id: int) -> bool:
        """Deactivate a principle if its track record is consistently poor.

        Args:
            principle_id: The database ID of the principle.
            user_id: ID of the owning user.

        Returns:
            True if the principle was deactivated.
        """
        p = self.get_principle(principle_id, user_id)
        if not p:
            return False
        if p["invalidated_count"] > p["validated_count"] * 2 and p["invalidated_count"] > 2:
            self.db.execute(
                "UPDATE principles SET active = FALSE WHERE id = ? AND user_id = ?",
                (principle_id, user_id),
            )
            self.db.connect().commit()
            _audit(self.db, "principle_deactivated", "principle", principle_id)
            return True
        return False

    def apply_to_score(self, matching_principles: list[dict], user_id: int) -> float:
        """Calculate total confidence score adjustment from matching principles.

        Args:
            matching_principles: List of principle dictionaries.
            user_id: ID of the owning user.

        Returns:
            Float representing the total confidence adjustment.
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
            self.db.execute(
                "UPDATE principles SET last_applied = ? WHERE id = ? AND user_id = ?",
                (now, p["id"], user_id),
            )
        self.db.connect().commit()
        return adjustment

    def check_trade_outcomes(self, user_id: int, lookback_days: int = 90) -> list[dict]:
        """Evaluate trade outcomes and validate/invalidate principles used.

        Args:
            user_id: ID of the owning user.
            lookback_days: Number of days to look back.

        Returns:
            List of dicts with trade_id, symbol, pnl, principle_id, action.
        """
        cutoff = datetime.now(UTC).isoformat()[:10]
        trades = self.db.fetchall(
            """
            SELECT t.id, t.symbol, t.realized_pnl, t.signal_id
            FROM trades t
            WHERE t.realized_pnl IS NOT NULL
              AND t.timestamp >= date(?, '-' || ? || ' days')
              AND t.user_id = ?
              AND t.id NOT IN (
                  SELECT CAST(json_extract(details, '$.trade_id') AS INTEGER)
                  FROM audit_log
                  WHERE action IN ('principle_validated', 'principle_invalidated')
                    AND json_extract(details, '$.trade_id') IS NOT NULL
              )
            ORDER BY t.timestamp
            """,
            (cutoff, lookback_days, user_id),
        )

        results = []
        active_principles = self.get_all(user_id, active_only=True)

        for trade in trades:
            pnl = trade.get("realized_pnl", 0) or 0
            win = pnl > 0

            for p in active_principles:
                if win:
                    self.validate_principle(p["id"], user_id)
                    results.append(
                        {
                            "trade_id": trade["id"],
                            "symbol": trade["symbol"],
                            "pnl": pnl,
                            "principle_id": p["id"],
                            "action": "validated",
                        }
                    )
                else:
                    self.invalidate_principle(p["id"], user_id)
                    self.deactivate_if_poor(p["id"], user_id)
                    results.append(
                        {
                            "trade_id": trade["id"],
                            "symbol": trade["symbol"],
                            "pnl": pnl,
                            "principle_id": p["id"],
                            "action": "invalidated",
                        }
                    )

        return results

    def adjust_weights(self, user_id: int) -> list[dict]:
        """Adjust principle weights based on their track record.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of dicts with id, text, old_weight, new_weight.
        """
        principles = self.get_all(user_id, active_only=True)
        adjustments = []

        for p in principles:
            total = p["validated_count"] + p["invalidated_count"]
            if total < 3:
                continue

            win_rate = p["validated_count"] / total
            old_weight = p["weight"]

            factor = 1.0 + (win_rate - 0.5) * 0.2
            new_weight = max(0.01, min(0.20, old_weight * factor))
            new_weight = round(new_weight, 4)

            if new_weight != old_weight:
                self.db.execute(
                    "UPDATE principles SET weight = ? WHERE id = ? AND user_id = ?",
                    (new_weight, p["id"], user_id),
                )
                adjustments.append(
                    {
                        "id": p["id"],
                        "text": p["text"],
                        "old_weight": old_weight,
                        "new_weight": new_weight,
                    }
                )

        if adjustments:
            self.db.connect().commit()

        return adjustments

    def discover_patterns(self, user_id: int) -> list[dict]:
        """Analyze trade outcomes for emerging patterns.

        Args:
            user_id: ID of the owning user.

        Returns:
            List of dicts describing discovered patterns.
        """
        patterns = []

        source_stats = self.db.fetchall(
            """
            SELECT s.source,
                   COUNT(*) as total,
                   SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades t
            JOIN signals s ON t.signal_id = s.id
            WHERE t.realized_pnl IS NOT NULL AND t.user_id = ?
            GROUP BY s.source
            HAVING COUNT(*) >= 5
            """,
            (user_id,),
        )

        for row in source_stats:
            win_rate = row["wins"] / row["total"] if row["total"] > 0 else 0
            if win_rate > 0.7 or win_rate < 0.3:
                patterns.append(
                    {
                        "pattern_type": "source_performance",
                        "description": (
                            f"Signal source '{row['source']}' has {win_rate:.0%} win rate"
                        ),
                        "win_rate": win_rate,
                        "sample_size": row["total"],
                    }
                )

        strategy_stats = self.db.fetchall(
            """
            SELECT th.strategy,
                   COUNT(*) as total,
                   SUM(CASE WHEN t.realized_pnl > 0 THEN 1 ELSE 0 END) as wins
            FROM trades t
            JOIN signals s ON t.signal_id = s.id
            JOIN theses th ON s.thesis_id = th.id
            WHERE t.realized_pnl IS NOT NULL AND t.user_id = ?
            GROUP BY th.strategy
            HAVING COUNT(*) >= 5
            """,
            (user_id,),
        )

        for row in strategy_stats:
            win_rate = row["wins"] / row["total"] if row["total"] > 0 else 0
            if win_rate > 0.7 or win_rate < 0.3:
                patterns.append(
                    {
                        "pattern_type": "strategy_performance",
                        "description": f"Strategy '{row['strategy']}' has {win_rate:.0%} win rate",
                        "win_rate": win_rate,
                        "sample_size": row["total"],
                    }
                )

        return patterns


def _audit(db: Database, action: str, entity_type: str, entity_id: int | None) -> None:
    """Create an audit log entry for a principles engine action."""
    db.execute(
        """INSERT INTO audit_log (actor, action, entity_type, entity_id)
           VALUES (?,?,?,?)""",
        (ActorType.ENGINE.value, action, entity_type, entity_id),
    )
    db.connect().commit()
