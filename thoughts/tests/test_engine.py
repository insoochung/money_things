"""Tests for ThoughtsEngine DB operations."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine import ThoughtsEngine


@pytest.fixture()
def engine(tmp_path: Path) -> ThoughtsEngine:
    """Create an engine with a temp DB."""
    return ThoughtsEngine(
        thoughts_db=tmp_path / "thoughts.db",
        moves_db=tmp_path / "moves.db",  # non-existent, that's OK
    )


class TestJournals:
    def test_create_and_get(self, engine: ThoughtsEngine) -> None:
        jid = engine.create_journal("Test", "Content here", "research")
        assert jid >= 1
        j = engine.get_journal(jid)
        assert j is not None
        assert j["title"] == "Test"
        assert j["journal_type"] == "research"

    def test_create_with_thesis(self, engine: ThoughtsEngine) -> None:
        jid = engine.create_journal("T", "C", "review", thesis_id=42, symbols=["AAPL"])
        j = engine.get_journal(jid)
        assert j["thesis_id"] == 42
        assert '"AAPL"' in j["symbols"]

    def test_list_journals(self, engine: ThoughtsEngine) -> None:
        engine.create_journal("A", "a", "research")
        engine.create_journal("B", "b", "thought")
        assert len(engine.list_journals()) == 2
        assert len(engine.list_journals(journal_type="research")) == 1

    def test_update_journal(self, engine: ThoughtsEngine) -> None:
        jid = engine.create_journal("T", "old", "thought")
        engine.update_journal(jid, "new content")
        j = engine.get_journal(jid)
        assert j["content"] == "new content"


class TestResearchNotes:
    def test_save_and_get(self, engine: ThoughtsEngine) -> None:
        rid = engine.save_research("AAPL", "Apple Research", "Good company", confidence=0.8)
        assert rid >= 1
        notes = engine.get_research("AAPL")
        assert len(notes) == 1
        assert notes[0]["confidence"] == 0.8

    def test_get_latest(self, engine: ThoughtsEngine) -> None:
        engine.save_research("MSFT", "Old", "old content")
        engine.save_research("MSFT", "New", "new content")
        latest = engine.get_latest_research("MSFT")
        assert latest is not None
        assert latest["title"] == "New"

    def test_case_insensitive(self, engine: ThoughtsEngine) -> None:
        engine.save_research("aapl", "Test", "content")
        assert len(engine.get_research("AAPL")) == 1


class TestThesisSessions:
    def test_create_and_get_active(self, engine: ThoughtsEngine) -> None:
        sid = engine.create_session(1, "thoughts-thesis-1")
        assert sid >= 1
        s = engine.get_active_session(1)
        assert s is not None
        assert s["session_key"] == "thoughts-thesis-1"

    def test_update_session(self, engine: ThoughtsEngine) -> None:
        sid = engine.create_session(2, "key")
        engine.update_session(sid, status="completed", summary="Done")
        assert engine.get_active_session(2) is None
        sessions = engine.list_sessions(status="completed")
        assert len(sessions) == 1
        assert sessions[0]["summary"] == "Done"


class TestThoughtLog:
    def test_add_and_list(self, engine: ThoughtsEngine) -> None:
        tid = engine.add_thought("Market feels frothy", tags=["macro"])
        assert tid >= 1
        thoughts = engine.list_thoughts()
        assert len(thoughts) == 1
        assert thoughts[0]["content"] == "Market feels frothy"

    def test_linked_thought(self, engine: ThoughtsEngine) -> None:
        engine.add_thought("META overvalued?", linked_symbol="meta", linked_thesis_id=5)
        t = engine.list_thoughts()[0]
        assert t["linked_symbol"] == "META"
        assert t["linked_thesis_id"] == 5


class TestMovesDBReaders:
    def test_no_moves_db(self, engine: ThoughtsEngine) -> None:
        """When moves DB doesn't exist, returns empty lists."""
        assert engine.get_positions() == []
        assert engine.get_theses() == []
        assert engine.get_signals() == []
        assert engine.get_recent_trades() == []
        assert engine.get_thesis(1) is None
