"""Tests for thoughts command handlers."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import commands
from engine import ThoughtsEngine


@pytest.fixture(autouse=True)
def _patch_engine(tmp_path: Path):
    """Patch commands to use temp DBs."""
    moves_db = tmp_path / "moves.db"
    conn = sqlite3.connect(str(moves_db))
    conn.executescript("""
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT, status TEXT DEFAULT 'active',
            symbols TEXT DEFAULT '[]', conviction REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, symbol TEXT, shares REAL DEFAULT 0,
            avg_cost REAL DEFAULT 0, side TEXT DEFAULT 'long',
            thesis_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY, action TEXT, symbol TEXT,
            thesis_id INTEGER, confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'manual', reasoning TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY, symbol TEXT, action TEXT,
            shares REAL, price REAL,
            timestamp TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO theses (title, symbols) VALUES ('AI inference', '["AMD"]');
    """)
    conn.commit()
    conn.close()

    engine = ThoughtsEngine(thoughts_db=tmp_path / "thoughts.db", moves_db=moves_db)

    with patch.object(commands, "_get_engine", return_value=engine):
        from bridge import ThoughtsBridge

        bridge = ThoughtsBridge(engine)
        with patch.object(commands, "_get_bridge", return_value=bridge):
            yield


def test_cmd_think_by_name():
    result = commands.cmd_think("AI inference")
    assert "Starting research" in result or "Resuming" in result


def test_cmd_think_not_found():
    result = commands.cmd_think("nonexistent thesis")
    assert "No thesis found" in result


def test_cmd_thought():
    result = commands.cmd_thought("Market seems overheated")
    assert "captured" in result


def test_cmd_journal_empty():
    result = commands.cmd_journal()
    assert "No journal" in result


def test_cmd_review_no_research():
    result = commands.cmd_review("AAPL")
    assert "No research" in result


def test_cmd_research():
    result = commands.cmd_research("AMD")
    assert "deep-dive" in result.lower() or "Starting" in result
