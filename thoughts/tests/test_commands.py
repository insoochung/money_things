"""Tests for thoughts command handlers (3-command structure)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import commands
from engine import ThoughtsEngine


def _create_moves_db(path: Path) -> None:
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
        CREATE TABLE watchlist_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id INTEGER REFERENCES theses(id),
            symbol TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            condition TEXT NOT NULL,
            target_value REAL NOT NULL,
            notes TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            triggered_at TEXT
        );
        INSERT INTO theses (title, symbols, conviction)
            VALUES ('AI inference', '["AMD"]', 0.6);
        INSERT INTO theses (title, symbols, conviction)
            VALUES ('Cloud security', '["CRWD","ZS"]', 0.75);
        INSERT INTO watchlist_triggers (thesis_id, symbol, trigger_type, condition, target_value)
            VALUES (1, 'AMD', 'entry', 'price_below', 180.0);
        INSERT INTO watchlist_triggers (thesis_id, symbol, trigger_type, condition, target_value)
            VALUES (1, 'AMD', 'stop_loss', 'price_below', 120.0);
    """)
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _patch_engine(tmp_path: Path):
    """Patch commands to use temp DBs."""
    moves_db = tmp_path / "moves.db"
    _create_moves_db(moves_db)

    # Write a minimal agent prompt for spawner
    prompt = tmp_path / "prompt.md"
    prompt.write_text("# Test Agent Prompt")

    engine = ThoughtsEngine(
        thoughts_db=tmp_path / "thoughts.db", moves_db=moves_db,
    )

    from bridge import ThoughtsBridge
    bridge = ThoughtsBridge(engine)

    with (
        patch.object(commands, "_get_engine", return_value=engine),
        patch.object(commands, "_get_bridge", return_value=bridge),
    ):
        yield


class TestCmdThink:
    def test_existing_thesis_returns_task(self) -> None:
        result = commands.cmd_think("AI inference")
        assert isinstance(result, dict)
        assert result["thesis_id"] is not None
        assert result["task"] is not None
        assert not result["is_new"]
        assert "Deepening" in result["message"]

    def test_new_idea_returns_task(self) -> None:
        result = commands.cmd_think("quantum computing")
        assert result["thesis_id"] is None
        assert result["is_new"]
        assert result["task"] is not None
        assert "New idea" in result["message"]

    def test_task_contains_context(self) -> None:
        result = commands.cmd_think("Cloud security")
        assert "CRWD" in result["task"]
        assert "Cloud security" in result["task"]


class TestCmdNote:
    def test_note_captured(self) -> None:
        result = commands.cmd_note("Market feels frothy today")
        assert "captured" in result

    def test_auto_links_by_symbol(self) -> None:
        result = commands.cmd_note("AMD earnings look strong")
        assert "thesis #1" in result
        assert "AMD" in result

    def test_auto_links_by_title_keyword(self) -> None:
        result = commands.cmd_note(
            "cloud security spending increasing"
        )
        assert "thesis #2" in result

    def test_no_link_when_unrelated(self) -> None:
        result = commands.cmd_note("nice weather today")
        assert "thesis" not in result.lower()


class TestCmdJournal:
    def test_empty_journal(self) -> None:
        result = commands.cmd_journal()
        assert "Active Theses" in result

    def test_shows_theses(self) -> None:
        result = commands.cmd_journal()
        assert "AI inference" in result
        assert "Cloud security" in result

    def test_shows_notes_after_adding(self) -> None:
        commands.cmd_note("Test observation")
        result = commands.cmd_journal()
        assert "Recent Notes" in result
        assert "Test observation" in result


# Sample valid sub-agent output JSON
_VALID_OUTPUT = """\
Here is my research analysis.

```json
{
  "research_summary": "AMD showing strong momentum in AI inference. MI300X gaining traction.",
  "thesis_update": {
    "title": null,
    "description": "AMD AI inference thesis strengthened by MI300X adoption",
    "status": "strengthening"
  },
  "ticker_recommendations": [
    {"symbol": "AMD", "action": "add", "reasoning": "MI300X ramp accelerating"},
    {"symbol": "NVDA", "action": "watch", "reasoning": "Competitor pressure"}
  ],
  "critic_assessment": "Risk: NVIDIA dominant. AMD needs sustained execution.",
  "conviction_change": {
    "old_value": 0.6,
    "new_value": 0.75,
    "reasoning": "MI300X traction validates thesis"
  }
}
```
"""

_INVALID_OUTPUT = "Sorry, I couldn't complete the research. No JSON here."


class TestCmdThinkResult:
    def test_parses_valid_output(self) -> None:
        result = commands.cmd_think_result(_VALID_OUTPUT, thesis_id=1)
        assert result["parsed"] is True
        assert "Research complete" in result["message"]
        assert "AMD" in result["message"]
        assert len(result["applied"]) > 0
        assert len(result["pending"]) > 0

    def test_returns_buttons_for_pending(self) -> None:
        result = commands.cmd_think_result(_VALID_OUTPUT, thesis_id=1)
        assert len(result["buttons"]) >= 2
        # Should have approve and reject buttons for conviction
        cb_data = [b["callback_data"] for b in result["buttons"]]
        assert any("think_approve:conviction:1" in d for d in cb_data)
        assert any("think_reject:conviction:1" in d for d in cb_data)
        # Should have thesis update buttons too
        assert any("think_approve:thesis:1" in d for d in cb_data)

    def test_handles_invalid_output(self) -> None:
        result = commands.cmd_think_result(_INVALID_OUTPUT, thesis_id=1)
        assert result["parsed"] is False
        assert "Could not parse" in result["message"]
        assert result["buttons"] == []

    def test_no_thesis_id_skips_db(self) -> None:
        result = commands.cmd_think_result(_VALID_OUTPUT, thesis_id=None)
        assert result["parsed"] is True
        assert "standalone" in result["message"]
        assert result["applied"] == []
        assert result["pending"] == []

    def test_conviction_in_summary(self) -> None:
        result = commands.cmd_think_result(_VALID_OUTPUT, thesis_id=1)
        assert "Conviction" in result["message"]
        assert "75%" in result["message"]

    def test_auto_applied_changes(self) -> None:
        result = commands.cmd_think_result(_VALID_OUTPUT, thesis_id=1)
        applied = result["applied"]
        assert any("Research summary" in a for a in applied)
        assert any("Critic" in a for a in applied)
        assert any("AMD" in a for a in applied)


class TestCmdThinkApprove:
    def test_approve_conviction(self) -> None:
        result = commands.cmd_think_approve("think_approve:conviction:1:75")
        assert "updated" in result.lower() or "✅" in result
        # Verify DB was updated
        engine = commands._get_engine()
        thesis = engine.get_thesis(1)
        assert thesis is not None
        assert thesis["conviction"] == 75

    def test_approve_thesis(self) -> None:
        result = commands.cmd_think_approve("think_approve:thesis:1")
        assert "✅" in result

    def test_invalid_data(self) -> None:
        result = commands.cmd_think_approve("bad")
        assert "❌" in result


class TestCmdThinkReject:
    def test_reject_conviction(self) -> None:
        result = commands.cmd_think_reject("think_reject:conviction:1")
        assert "skipped" in result.lower()

    def test_reject_thesis(self) -> None:
        result = commands.cmd_think_reject("think_reject:thesis:1")
        assert "skipped" in result.lower()

    def test_invalid_data(self) -> None:
        result = commands.cmd_think_reject("bad")
        assert "❌" in result


class TestCmdBrief:
    def test_brief_returns_string(self) -> None:
        mock_prices = {"AMD": 165.0, "CRWD": 400.0, "ZS": 220.0}
        with patch.object(commands, "_fetch_prices", return_value=mock_prices):
            result = commands.cmd_brief()
        assert isinstance(result, str)
        assert "Daily Brief" in result

    def test_brief_shows_theses(self) -> None:
        mock_prices = {"AMD": 165.0, "CRWD": 400.0, "ZS": 220.0}
        with patch.object(commands, "_fetch_prices", return_value=mock_prices):
            result = commands.cmd_brief()
        assert "AI inference" in result
        assert "Cloud security" in result

    def test_brief_shows_prices(self) -> None:
        with patch.object(commands, "_fetch_prices", return_value={"AMD": 165.50}):
            result = commands.cmd_brief()
        assert "$165.50" in result

    def test_brief_shows_trigger_proximity(self) -> None:
        with patch.object(commands, "_fetch_prices", return_value={"AMD": 175.0}):
            result = commands.cmd_brief()
        assert "Trigger Proximity" in result
        assert "entry" in result
        # AMD at 175, entry trigger at 180 → ~2.9% away → should show CLOSE warning
        assert "CLOSE" in result

    def test_brief_shows_stop_loss_distance(self) -> None:
        with patch.object(commands, "_fetch_prices", return_value={"AMD": 165.0}):
            result = commands.cmd_brief()
        # AMD at 165, stop loss at 120 → ~27% away → no alert
        assert "stop loss" in result

    def test_brief_empty_portfolio(self) -> None:
        engine = commands._get_engine()
        # Clear theses
        with patch.object(engine, "get_theses", return_value=[]):
            with patch.object(commands, "_fetch_prices", return_value={}):
                result = commands.cmd_brief()
        # Should still return something (triggers remain)
        assert "Brief" in result or "Nothing" in result

    def test_brief_with_pending_signals(self) -> None:
        engine = commands._get_engine()
        # Insert a pending signal
        engine._moves_query(
            "INSERT INTO signals (action, symbol, thesis_id, status, reasoning) "
            "VALUES ('BUY', 'AMD', 1, 'pending', 'test signal')"
        )
        # Actually need to use execute for INSERT
        import sqlite3
        conn = sqlite3.connect(str(engine.moves_db))
        conn.execute(
            "INSERT INTO signals (action, symbol, thesis_id, status, reasoning) "
            "VALUES ('BUY', 'AMD', 1, 'pending', 'test signal')"
        )
        conn.commit()
        conn.close()

        with patch.object(commands, "_fetch_prices", return_value={"AMD": 165.0}):
            result = commands.cmd_brief()
        assert "Pending Signals" in result

    def test_brief_no_yfinance_earnings_graceful(self) -> None:
        """Brief should work even if earnings fetch fails."""
        with patch.object(commands, "_fetch_prices", return_value={"AMD": 165.0}):
            with patch("yfinance.Ticker", side_effect=Exception("no network")):
                result = commands.cmd_brief()
        assert "Daily Brief" in result

    def test_fetch_prices_mock(self) -> None:
        """_fetch_prices handles failures gracefully."""
        with patch("yfinance.Ticker") as mock_ticker:
            mock_info = type("FastInfo", (), {"last_price": 150.0})()
            mock_ticker.return_value.fast_info = mock_info
            prices = commands._fetch_prices(["AMD"])
        assert prices.get("AMD") == 150.0

    def test_fetch_prices_failure(self) -> None:
        """_fetch_prices returns empty on error."""
        with patch("yfinance.Ticker", side_effect=Exception("fail")):
            prices = commands._fetch_prices(["AMD"])
        assert prices == {}
