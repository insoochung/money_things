"""Bridge between money_thoughts and money_moves.

Provides bidirectional communication: reading portfolio context from moves DB
and pushing thesis updates back.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from engine import ThoughtsEngine


class ThoughtsBridge:
    """Bidirectional bridge between thoughts and moves modules.

    Args:
        engine: ThoughtsEngine instance (provides DB connections).
    """

    def __init__(self, engine: ThoughtsEngine | None = None) -> None:
        self.engine = engine or ThoughtsEngine()

    # ── Read from Moves ───────────────────────────────────────

    def get_portfolio_context(self) -> dict[str, Any]:
        """Get full portfolio context from moves DB.

        Returns:
            Dict with positions, theses, and recent signals.
        """
        return {
            "positions": self.engine.get_positions(),
            "theses": self.engine.get_theses(),
            "signals": self.engine.get_signals(status="pending"),
            "recent_trades": self.engine.get_recent_trades(limit=10),
        }

    def get_thesis_context(self, thesis_id: int) -> dict[str, Any] | None:
        """Get full context for a specific thesis.

        Returns:
            Dict with thesis details, signals, positions, and research notes.
        """
        thesis = self.engine.get_thesis(thesis_id)
        if not thesis:
            return None

        # Parse symbols from thesis
        symbols: list[str] = []
        if thesis.get("symbols"):
            try:
                symbols = json.loads(thesis["symbols"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Get positions for thesis symbols
        positions = [
            p for p in self.engine.get_positions() if p["symbol"] in symbols
        ]

        # Get signals for this thesis
        signals = self.engine.get_signals(thesis_id=thesis_id)

        # Get research notes from thoughts DB
        research: list[dict[str, Any]] = []
        for sym in symbols:
            notes = self.engine.get_research(sym)
            if notes:
                research.extend(notes)

        # Get journals for this thesis
        journals = self.engine.list_journals(thesis_id=thesis_id)

        return {
            "thesis": thesis,
            "symbols": symbols,
            "positions": positions,
            "signals": signals,
            "research": research,
            "journals": journals,
        }

    # ── Write to Moves ────────────────────────────────────────

    def push_thesis_update(
        self,
        thesis_id: int,
        conviction: float,
        status: str,
        reasoning: str,
    ) -> bool:
        """Push a thesis conviction/status update to the moves DB.

        Args:
            thesis_id: The thesis ID in the moves DB.
            conviction: New conviction score (0.0-1.0).
            status: New status (strengthening/weakening/confirmed/invalidated).
            reasoning: Why the update is being made.

        Returns:
            True if the update was applied.
        """
        from engine import _connect

        if not self.engine.moves_db.exists():
            return False

        # Map thoughts status to moves status
        status_map = {
            "strengthening": "active",
            "weakening": "active",
            "confirmed": "validated",
            "invalidated": "invalidated",
        }
        moves_status = status_map.get(status, "active")

        with _connect(self.engine.moves_db) as conn:
            conn.execute(
                "UPDATE theses SET conviction = ?, status = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (conviction, moves_status, thesis_id),
            )
            conn.commit()

        # Also log as a journal entry in thoughts DB
        self.engine.create_journal(
            title=f"Thesis #{thesis_id} update: {status}",
            content=f"Conviction: {conviction}\nStatus: {status}\n\n{reasoning}",
            journal_type="review",
            thesis_id=thesis_id,
        )
        return True

    # ── Write to Thoughts ─────────────────────────────────────

    def save_research(
        self,
        symbol: str,
        content: str,
        thesis_id: int | None = None,
        title: str | None = None,
    ) -> int:
        """Save research note to thoughts DB.

        Returns:
            The new research note ID.
        """
        return self.engine.save_research(
            symbol=symbol,
            title=title or f"Research: {symbol.upper()}",
            content=content,
            thesis_id=thesis_id,
        )

    def save_journal(
        self,
        content: str,
        journal_type: str,
        thesis_id: int | None = None,
        title: str | None = None,
    ) -> int:
        """Save journal entry to thoughts DB.

        Returns:
            The new journal ID.
        """
        return self.engine.create_journal(
            title=title or f"{journal_type.title()} — {datetime.now():%Y-%m-%d}",
            content=content,
            journal_type=journal_type,
            thesis_id=thesis_id,
        )
