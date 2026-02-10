"""Database layer for thoughts module using the proper Database class.

This module provides a unified database interface for the thoughts module
that uses the Database class from moves/db/database.py instead of raw
sqlite3 connections.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Add moves to path to import Database
sys.path.append(str(Path(__file__).parent.parent.parent / "moves"))

from db.database import Database

logger = logging.getLogger(__name__)

# Database paths
THOUGHTS_DB_PATH = Path(__file__).parent.parent / "data" / "thoughts.db"
MOVES_DB_PATH = Path(__file__).parent.parent.parent / "moves" / "data" / "moves.db"
MOVES_MOCK_DB_PATH = Path(__file__).parent.parent.parent / "moves" / "data" / "moves_mock.db"


class ThoughtsDatabase:
    """Database manager for thoughts module using proper Database class."""

    def __init__(self) -> None:
        self.thoughts_db = Database(THOUGHTS_DB_PATH)

        # Try live moves DB first, fall back to mock
        if MOVES_DB_PATH.exists():
            self.moves_db = Database(MOVES_DB_PATH)
        else:
            self.moves_db = Database(MOVES_MOCK_DB_PATH)

        self._ensure_thoughts_schema()

    def _ensure_thoughts_schema(self) -> None:
        """Ensure thoughts database schema exists."""
        schema_sql = """
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

        CREATE TABLE IF NOT EXISTS thought_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thought_type TEXT NOT NULL,
            content TEXT NOT NULL,
            symbols TEXT,
            thesis_id INTEGER,
            confidence REAL,
            tags TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS feedback_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_type TEXT NOT NULL,
            thesis_id INTEGER,
            questions TEXT NOT NULL,
            responses TEXT,
            insights TEXT,
            next_actions TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            completed_at TEXT
        );
        """

        try:
            with self.thoughts_db.transaction():
                for statement in schema_sql.strip().split(';'):
                    if statement.strip():
                        self.thoughts_db.execute(statement)
        except Exception as e:
            logger.error(f"Failed to create thoughts schema: {e}")
            raise
