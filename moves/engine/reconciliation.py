"""Position reconciliation between local database and broker.

Compares DB-tracked positions against the broker's reported positions to detect
and optionally fix discrepancies. Designed to run as a daily scheduled check.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from db.database import Database
from engine import ActorType

logger = logging.getLogger(__name__)

MINOR_SHARE_THRESHOLD = 0.01


def _audit(db: Database, action: str, detail: str) -> None:
    """Create an audit log entry."""
    now = datetime.now(UTC).isoformat()
    db.execute(
        "INSERT INTO audit_log (actor, action, detail, timestamp) VALUES (?, ?, ?, ?)",
        (ActorType.ENGINE, action, detail, now),
    )


class Reconciler:
    """Reconcile DB positions with broker positions.

    Detects share count mismatches, positions missing from either side,
    and can auto-sync minor discrepancies (rounding differences).

    Attributes:
        db: Database instance for local position data.
        broker: Broker instance for fetching live positions.
    """

    def __init__(self, db: Database, broker: Any) -> None:
        """Initialize the reconciler.

        Args:
            db: Database instance.
            broker: Broker for fetching live positions.
        """
        self.db = db
        self.broker = broker

    async def reconcile(self) -> dict[str, Any]:
        """Compare DB vs broker positions and return discrepancies.

        Returns:
            Dictionary with 'matched', 'discrepancies', 'db_only', and 'broker_only' lists.
        """
        broker_positions = await self.broker.get_positions()
        broker_map = {p.symbol: p for p in broker_positions}

        db_rows = self.db.fetch_all(
            "SELECT symbol, shares, avg_cost FROM positions WHERE shares > 0"
        )
        db_map = {r["symbol"]: r for r in db_rows}

        result: dict[str, Any] = {
            "matched": [],
            "discrepancies": [],
            "db_only": [],
            "broker_only": [],
        }

        all_symbols = set(broker_map.keys()) | set(db_map.keys())

        for symbol in sorted(all_symbols):
            in_broker = symbol in broker_map
            in_db = symbol in db_map

            if in_broker and in_db:
                db_shares = db_map[symbol]["shares"]
                broker_shares = broker_map[symbol].shares
                if abs(db_shares - broker_shares) <= MINOR_SHARE_THRESHOLD:
                    result["matched"].append(symbol)
                else:
                    result["discrepancies"].append(
                        {
                            "symbol": symbol,
                            "db_shares": db_shares,
                            "broker_shares": broker_shares,
                            "diff": broker_shares - db_shares,
                        }
                    )
            elif in_db:
                result["db_only"].append({"symbol": symbol, "shares": db_map[symbol]["shares"]})
            else:
                result["broker_only"].append(
                    {"symbol": symbol, "shares": broker_map[symbol].shares}
                )

        _audit(
            self.db,
            "reconciliation",
            f"Matched: {len(result['matched'])}, Discrepancies: {len(result['discrepancies'])}, "
            f"DB-only: {len(result['db_only'])}, Broker-only: {len(result['broker_only'])}",
        )
        return result

    async def auto_sync(self, discrepancies: list[dict[str, Any]]) -> int:
        """Fix minor discrepancies by updating DB to match broker.

        Only syncs discrepancies where the absolute difference is small
        (< 1 share), treating them as rounding differences.

        Args:
            discrepancies: List of discrepancy dicts from reconcile().

        Returns:
            Number of positions synced.
        """
        synced = 0
        for d in discrepancies:
            if abs(d["diff"]) < 1.0:
                self.db.execute(
                    "UPDATE positions SET shares = ? WHERE symbol = ? AND shares > 0",
                    (d["broker_shares"], d["symbol"]),
                )
                _audit(
                    self.db,
                    "auto_sync",
                    f"{d['symbol']}: {d['db_shares']} -> {d['broker_shares']}",
                )
                synced += 1
                logger.info(
                    "Auto-synced %s: %.4f -> %.4f",
                    d["symbol"],
                    d["db_shares"],
                    d["broker_shares"],
                )
        return synced

    async def daily_check(self) -> dict[str, Any]:
        """Run daily reconciliation and auto-sync minor issues.

        Returns:
            Reconciliation result with auto_synced count added.
        """
        result = await self.reconcile()
        if result["discrepancies"]:
            synced = await self.auto_sync(result["discrepancies"])
            result["auto_synced"] = synced
        else:
            result["auto_synced"] = 0
        logger.info("Daily reconciliation complete: %s", result)
        return result
