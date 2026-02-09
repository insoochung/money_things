"""Tests for context_builder module."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bridge import ThoughtsBridge
from context_builder import (
    build_context,
    build_new_idea_context,
    compute_slow_to_act_gates,
    find_thesis_by_idea,
    format_context_packet,
    gather_thesis_context,
)
from engine import ThoughtsEngine


def _create_moves_db(path: Path) -> None:
    """Create a minimal moves DB with test data."""
    conn = sqlite3.connect(str(path))
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
        INSERT INTO theses (title, symbols, conviction)
            VALUES ('AI cybersecurity', '["CRWD","FTNT"]', 0.65);
        INSERT INTO theses (title, symbols, conviction)
            VALUES ('Semiconductor supercycle', '["AMD","NVDA"]', 0.8);
        INSERT INTO positions (symbol, shares, avg_cost)
            VALUES ('CRWD', 10, 250.0);
        INSERT INTO signals (action, symbol, thesis_id, status)
            VALUES ('BUY', 'FTNT', 1, 'pending');
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def setup(tmp_path: Path):
    """Create engine and bridge with test data."""
    moves_db = tmp_path / "moves.db"
    _create_moves_db(moves_db)
    engine = ThoughtsEngine(
        thoughts_db=tmp_path / "thoughts.db",
        moves_db=moves_db,
    )
    bridge = ThoughtsBridge(engine)
    return engine, bridge


class TestFindThesis:
    def test_find_by_name_substring(self, setup) -> None:
        engine, _ = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        assert thesis is not None
        assert thesis["title"] == "AI cybersecurity"

    def test_find_by_id(self, setup) -> None:
        engine, _ = setup
        thesis = find_thesis_by_idea(engine, "2")
        assert thesis is not None
        assert "Semiconductor" in thesis["title"]

    def test_not_found(self, setup) -> None:
        engine, _ = setup
        assert find_thesis_by_idea(engine, "quantum computing") is None

    def test_case_insensitive(self, setup) -> None:
        engine, _ = setup
        assert find_thesis_by_idea(engine, "AI CYBER") is not None


class TestGatherContext:
    def test_includes_positions_for_thesis_symbols(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        assert len(ctx["positions"]) == 1
        assert ctx["positions"][0]["symbol"] == "CRWD"

    def test_includes_signals(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        assert len(ctx["signals"]) == 1
        assert ctx["signals"][0]["symbol"] == "FTNT"

    def test_includes_linked_thoughts(self, setup) -> None:
        engine, bridge = setup
        engine.add_thought(
            "CRWD earnings look strong",
            linked_thesis_id=1, linked_symbol="CRWD",
        )
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        assert len(ctx["linked_thoughts"]) == 1

    def test_no_positions_for_unrelated_thesis(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "Semiconductor")
        ctx = gather_thesis_context(engine, bridge, thesis)
        # No AMD/NVDA positions in test data
        assert ctx["positions"] == []


class TestNewIdeaContext:
    def test_new_idea_has_no_thesis(self) -> None:
        ctx = build_new_idea_context("quantum computing")
        assert ctx["thesis"] is None
        assert ctx["idea"] == "quantum computing"
        assert ctx["positions"] == []

    def test_format_new_idea(self) -> None:
        ctx = build_new_idea_context("space economy")
        text = format_context_packet(ctx)
        assert "New Idea" in text
        assert "space economy" in text


class TestSlowToActGates:
    def test_new_thesis_cannot_signal(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        gates = compute_slow_to_act_gates(ctx)
        assert gates["session_count"] == 0
        assert not gates["meets_session_minimum"]
        assert not gates["can_generate_signals"]

    def test_mature_thesis_with_sessions(self, setup) -> None:
        engine, bridge = setup
        # Create 2 completed sessions
        s1 = engine.create_session(1, "key1")
        engine.update_session(s1, status="completed", summary="s1")
        s2 = engine.create_session(1, "key2")
        engine.update_session(s2, status="completed", summary="s2")

        # Manually backdate thesis creation for cooldown
        from engine import _connect
        with _connect(engine.moves_db) as conn:
            old = (datetime.now() - timedelta(weeks=2)).isoformat()
            conn.execute(
                "UPDATE theses SET created_at = ? WHERE id = 1",
                (old,),
            )
            conn.commit()

        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        gates = compute_slow_to_act_gates(ctx)
        assert gates["session_count"] == 2
        assert gates["meets_session_minimum"]
        # conviction is 0.65, below 0.7 threshold
        assert not gates["meets_conviction_threshold"]
        assert not gates["can_generate_signals"]


class TestFormatContextPacket:
    def test_existing_thesis_format(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        text = format_context_packet(ctx)
        assert "AI cybersecurity" in text
        assert "Slow-to-Act" in text
        assert "CRWD" in text

    def test_includes_all_sections(self, setup) -> None:
        engine, bridge = setup
        thesis = find_thesis_by_idea(engine, "cybersecurity")
        ctx = gather_thesis_context(engine, bridge, thesis)
        text = format_context_packet(ctx)
        assert "Positions" in text
        assert "Recent Notes" in text
        assert "Past Sessions" in text


class TestBuildContext:
    def test_existing_thesis(self, setup) -> None:
        engine, bridge = setup
        ctx, text = build_context(engine, bridge, "cybersecurity")
        assert ctx["thesis"] is not None
        assert "AI cybersecurity" in text

    def test_new_idea(self, setup) -> None:
        engine, bridge = setup
        ctx, text = build_context(engine, bridge, "quantum computing")
        assert ctx["thesis"] is None
        assert "quantum computing" in text
