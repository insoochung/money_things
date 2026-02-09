"""Tests for ThoughtsBridge."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bridge import ThoughtsBridge
from engine import ThoughtsEngine


@pytest.fixture()
def setup(tmp_path: Path) -> tuple[ThoughtsEngine, ThoughtsBridge, Path]:
    """Create engine and bridge with temp DBs, including a mock moves DB."""
    moves_db = tmp_path / "moves.db"
    # Create a minimal moves DB
    conn = sqlite3.connect(str(moves_db))
    conn.executescript("""
        CREATE TABLE theses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            thesis_text TEXT DEFAULT '',
            strategy TEXT DEFAULT 'long',
            status TEXT DEFAULT 'active',
            symbols TEXT DEFAULT '[]',
            conviction REAL DEFAULT 0.5,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            shares REAL NOT NULL DEFAULT 0,
            avg_cost REAL NOT NULL DEFAULT 0,
            side TEXT NOT NULL DEFAULT 'long',
            thesis_id INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            symbol TEXT NOT NULL,
            thesis_id INTEGER,
            confidence REAL DEFAULT 0.5,
            source TEXT DEFAULT 'manual',
            reasoning TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            shares REAL NOT NULL,
            price REAL NOT NULL,
            timestamp TEXT DEFAULT (datetime('now'))
        );

        INSERT INTO theses (title, symbols, conviction) VALUES ('AI thesis', '["NVDA","AMD"]', 0.7);
        INSERT INTO positions (symbol, shares, avg_cost) VALUES ('NVDA', 100, 450.0);
        INSERT INTO positions (symbol, shares, avg_cost) VALUES ('AMD', 50, 150.0);
    """)
    conn.commit()
    conn.close()

    engine = ThoughtsEngine(thoughts_db=tmp_path / "thoughts.db", moves_db=moves_db)
    bridge = ThoughtsBridge(engine)
    return engine, bridge, moves_db


def test_get_portfolio_context(setup: tuple) -> None:
    _, bridge, _ = setup
    ctx = bridge.get_portfolio_context()
    assert len(ctx["positions"]) == 2
    assert len(ctx["theses"]) == 1
    assert ctx["theses"][0]["title"] == "AI thesis"


def test_get_thesis_context(setup: tuple) -> None:
    _, bridge, _ = setup
    ctx = bridge.get_thesis_context(1)
    assert ctx is not None
    assert ctx["thesis"]["title"] == "AI thesis"
    assert ctx["symbols"] == ["NVDA", "AMD"]
    assert len(ctx["positions"]) == 2


def test_get_thesis_context_not_found(setup: tuple) -> None:
    _, bridge, _ = setup
    assert bridge.get_thesis_context(999) is None


def test_push_thesis_update(setup: tuple) -> None:
    engine, bridge, moves_db = setup
    result = bridge.push_thesis_update(
        thesis_id=1, conviction=0.9, status="strengthening", reasoning="Strong earnings"
    )
    assert result is True

    # Verify moves DB was updated
    conn = sqlite3.connect(str(moves_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT conviction, status FROM theses WHERE id = 1").fetchone()
    assert row["conviction"] == 0.9
    assert row["status"] == "active"  # strengthening maps to active
    conn.close()

    # Verify journal was created
    journals = engine.list_journals(thesis_id=1)
    assert len(journals) == 1


def test_save_research(setup: tuple) -> None:
    engine, bridge, _ = setup
    rid = bridge.save_research("NVDA", "Great quarter", thesis_id=1)
    assert rid >= 1
    notes = engine.get_research("NVDA")
    assert len(notes) == 1


def test_save_journal(setup: tuple) -> None:
    engine, bridge, _ = setup
    jid = bridge.save_journal("Reviewed AI thesis", "review", thesis_id=1)
    assert jid >= 1
