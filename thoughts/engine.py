"""Core engine for the money_thoughts module.

Manages journals, research notes, thesis sessions, and thought logs
in a dedicated SQLite database. Reads from the moves DB for portfolio context.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Thoughts DB
THOUGHTS_DB_PATH = Path(__file__).parent / "data" / "thoughts.db"

# Moves DB (read-only access)
MOVES_DB_PATH = Path(__file__).parent.parent / "moves" / "data" / "moves.db"
MOVES_MOCK_DB_PATH = Path(__file__).parent.parent / "moves" / "data" / "moves_mock.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS journals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    journal_type TEXT NOT NULL,
    symbols TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS research_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    thesis_id INTEGER,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    bull_case TEXT,
    bear_case TEXT,
    catalysts TEXT,
    risks TEXT,
    fair_value_estimate REAL,
    confidence REAL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS thesis_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id INTEGER,
    session_key TEXT,
    status TEXT DEFAULT 'active',
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    last_active TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS thought_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tags TEXT,
    linked_thesis_id INTEGER,
    linked_symbol TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a SQLite connection with Row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


class ThoughtsEngine:
    """Core engine for investment research journals and notes.

    Args:
        thoughts_db: Path to the thoughts SQLite database.
        moves_db: Path to the moves SQLite database (read-only).
    """

    def __init__(
        self,
        thoughts_db: Path | None = None,
        moves_db: Path | None = None,
    ) -> None:
        self.thoughts_db = thoughts_db or THOUGHTS_DB_PATH
        # Try live moves DB first, fall back to mock
        if moves_db:
            self.moves_db = moves_db
        elif MOVES_DB_PATH.exists():
            self.moves_db = MOVES_DB_PATH
        else:
            self.moves_db = MOVES_MOCK_DB_PATH
        self._init_db()

    def _init_db(self) -> None:
        """Create thoughts DB tables if they don't exist."""
        with _connect(self.thoughts_db) as conn:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    # ── Journals ──────────────────────────────────────────────

    def create_journal(
        self,
        title: str,
        content: str,
        journal_type: str,
        thesis_id: int | None = None,
        symbols: list[str] | None = None,
    ) -> int:
        """Create a journal entry. Returns the new journal ID."""
        symbols_json = json.dumps(symbols) if symbols else None
        with _connect(self.thoughts_db) as conn:
            cur = conn.execute(
                "INSERT INTO journals (thesis_id, title, content, journal_type, symbols) "
                "VALUES (?, ?, ?, ?, ?)",
                (thesis_id, title, content, journal_type, symbols_json),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_journal(self, journal_id: int) -> dict[str, Any] | None:
        """Get a journal entry by ID."""
        with _connect(self.thoughts_db) as conn:
            row = conn.execute("SELECT * FROM journals WHERE id = ?", (journal_id,)).fetchone()
            return dict(row) if row else None

    def list_journals(
        self,
        thesis_id: int | None = None,
        journal_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List journal entries with optional filters."""
        query = "SELECT * FROM journals WHERE 1=1"
        params: list[Any] = []
        if thesis_id is not None:
            query += " AND thesis_id = ?"
            params.append(thesis_id)
        if journal_type is not None:
            query += " AND journal_type = ?"
            params.append(journal_type)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with _connect(self.thoughts_db) as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def update_journal(self, journal_id: int, content: str) -> None:
        """Update journal content."""
        with _connect(self.thoughts_db) as conn:
            conn.execute(
                "UPDATE journals SET content = ?, updated_at = datetime('now') WHERE id = ?",
                (content, journal_id),
            )
            conn.commit()

    # ── Research Notes ────────────────────────────────────────

    def save_research(
        self,
        symbol: str,
        title: str,
        content: str,
        thesis_id: int | None = None,
        bull_case: str | None = None,
        bear_case: str | None = None,
        catalysts: list[str] | None = None,
        risks: list[str] | None = None,
        fair_value_estimate: float | None = None,
        confidence: float | None = None,
    ) -> int:
        """Save a research note. Returns the new note ID."""
        with _connect(self.thoughts_db) as conn:
            cur = conn.execute(
                "INSERT INTO research_notes "
                "(symbol, thesis_id, title, content, bull_case, bear_case, "
                "catalysts, risks, fair_value_estimate, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    symbol.upper(),
                    thesis_id,
                    title,
                    content,
                    bull_case,
                    bear_case,
                    json.dumps(catalysts) if catalysts else None,
                    json.dumps(risks) if risks else None,
                    fair_value_estimate,
                    confidence,
                ),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_research(self, symbol: str) -> list[dict[str, Any]]:
        """Get all research notes for a symbol, newest first."""
        with _connect(self.thoughts_db) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM research_notes WHERE symbol = ? ORDER BY id DESC",
                    (symbol.upper(),),
                ).fetchall()
            ]

    def get_latest_research(self, symbol: str) -> dict[str, Any] | None:
        """Get the most recent research note for a symbol."""
        with _connect(self.thoughts_db) as conn:
            row = conn.execute(
                "SELECT * FROM research_notes WHERE symbol = ? ORDER BY id DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
            return dict(row) if row else None

    # ── Thesis Sessions ───────────────────────────────────────

    def create_session(
        self,
        thesis_id: int,
        session_key: str,
    ) -> int:
        """Create a thesis research session. Returns session ID."""
        with _connect(self.thoughts_db) as conn:
            cur = conn.execute(
                "INSERT INTO thesis_sessions (thesis_id, session_key) VALUES (?, ?)",
                (thesis_id, session_key),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_active_session(self, thesis_id: int) -> dict[str, Any] | None:
        """Get the active session for a thesis."""
        with _connect(self.thoughts_db) as conn:
            row = conn.execute(
                "SELECT * FROM thesis_sessions "
                "WHERE thesis_id = ? AND status = 'active' "
                "ORDER BY last_active DESC LIMIT 1",
                (thesis_id,),
            ).fetchone()
            return dict(row) if row else None

    def update_session(
        self,
        session_id: int,
        status: str | None = None,
        summary: str | None = None,
    ) -> None:
        """Update session status and/or summary."""
        parts: list[str] = ["last_active = datetime('now')"]
        params: list[Any] = []
        if status is not None:
            parts.append("status = ?")
            params.append(status)
        if summary is not None:
            parts.append("summary = ?")
            params.append(summary)
        params.append(session_id)
        with _connect(self.thoughts_db) as conn:
            conn.execute(
                f"UPDATE thesis_sessions SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            conn.commit()

    def list_sessions(self, status: str = "active") -> list[dict[str, Any]]:
        """List thesis sessions by status."""
        with _connect(self.thoughts_db) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM thesis_sessions WHERE status = ? ORDER BY last_active DESC",
                    (status,),
                ).fetchall()
            ]

    # ── Thought Log ───────────────────────────────────────────

    def add_thought(
        self,
        content: str,
        tags: list[str] | None = None,
        linked_thesis_id: int | None = None,
        linked_symbol: str | None = None,
    ) -> int:
        """Capture a quick thought. Returns thought ID."""
        with _connect(self.thoughts_db) as conn:
            cur = conn.execute(
                "INSERT INTO thought_log (content, tags, linked_thesis_id, linked_symbol) "
                "VALUES (?, ?, ?, ?)",
                (
                    content,
                    json.dumps(tags) if tags else None,
                    linked_thesis_id,
                    linked_symbol.upper() if linked_symbol else None,
                ),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def list_thoughts(self, limit: int = 20) -> list[dict[str, Any]]:
        """List recent thoughts."""
        with _connect(self.thoughts_db) as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM thought_log ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            ]

    # ── Moves DB Readers (read-only) ─────────────────────────

    def _moves_query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a read-only query against the moves DB."""
        if not self.moves_db.exists():
            return []
        with _connect(self.moves_db) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _moves_query_one(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> dict[str, Any] | None:
        """Execute a read-only query returning one row."""
        rows = self._moves_query(sql, params)
        return rows[0] if rows else None

    def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions from moves DB."""
        return self._moves_query(
            "SELECT * FROM positions WHERE shares > 0 ORDER BY symbol"
        )

    def get_theses(self, status: str = "active") -> list[dict[str, Any]]:
        """Get theses from moves DB."""
        return self._moves_query(
            "SELECT * FROM theses WHERE status = ? ORDER BY updated_at DESC",
            (status,),
        )

    def get_thesis(self, thesis_id: int) -> dict[str, Any] | None:
        """Get a single thesis from moves DB."""
        return self._moves_query_one(
            "SELECT * FROM theses WHERE id = ?", (thesis_id,)
        )

    def get_signals(
        self, thesis_id: int | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Get signals from moves DB."""
        query = "SELECT * FROM signals WHERE 1=1"
        params: list[Any] = []
        if thesis_id is not None:
            query += " AND thesis_id = ?"
            params.append(thesis_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        return self._moves_query(query, tuple(params))

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get recent trades from moves DB."""
        return self._moves_query(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,)
        )

    def complete_session(self, session_id: int, summary: str = "") -> None:
        """Mark a session as completed with summary."""
        self.update_session(session_id, status="completed", summary=summary)

    def update_thesis_conviction(
        self, thesis_id: int, conviction: float
    ) -> bool:
        """Update thesis conviction in moves DB.

        Args:
            thesis_id: Thesis to update.
            conviction: New conviction (0-100 scale).

        Returns:
            True if a row was updated.
        """
        if not self.moves_db.exists():
            return False
        with _connect(self.moves_db) as conn:
            cur = conn.execute(
                "UPDATE theses SET conviction = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (conviction, thesis_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def update_thesis(
        self,
        thesis_id: int,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> bool:
        """Update thesis fields in moves DB.

        Args:
            thesis_id: Thesis to update.
            title: New title if provided.
            description: New thesis_text if provided.
            status: New status if provided.

        Returns:
            True if a row was updated.
        """
        if not self.moves_db.exists():
            return False
        parts: list[str] = ["updated_at = datetime('now')"]
        params: list[Any] = []
        if title is not None:
            parts.append("title = ?")
            params.append(title)
        if description is not None:
            parts.append("thesis_text = ?")
            params.append(description)
        if status is not None:
            parts.append("status = ?")
            params.append(status)
        params.append(thesis_id)
        with _connect(self.moves_db) as conn:
            cur = conn.execute(
                f"UPDATE theses SET {', '.join(parts)} WHERE id = ?",
                params,
            )
            conn.commit()
            return cur.rowcount > 0
