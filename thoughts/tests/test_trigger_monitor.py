"""Tests for trigger_monitor module."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import trigger_monitor
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
        CREATE TABLE watchlist_triggers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thesis_id INTEGER,
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
        INSERT INTO watchlist_triggers
            (thesis_id, symbol, trigger_type, condition, target_value, notes)
            VALUES (1, 'AMD', 'entry', 'price_below', 180.0, 'Buy the dip');
        INSERT INTO watchlist_triggers
            (thesis_id, symbol, trigger_type, condition, target_value)
            VALUES (1, 'AMD', 'stop_loss', 'price_below', 120.0);
        INSERT INTO watchlist_triggers
            (thesis_id, symbol, trigger_type, condition, target_value)
            VALUES (1, 'AMD', 'take_profit', 'price_above', 250.0);
    """)
    conn.commit()
    conn.close()


@pytest.fixture()
def engine(tmp_path: Path) -> ThoughtsEngine:
    moves_db = tmp_path / "moves.db"
    _create_moves_db(moves_db)
    return ThoughtsEngine(
        thoughts_db=tmp_path / "thoughts.db",
        moves_db=moves_db,
    )


class TestCheckTriggers:
    def test_critical_alert(self, engine: ThoughtsEngine) -> None:
        """AMD at 178 → entry at 180 is ~1.1% away → critical."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={"AMD": 178.0}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        critical = [a for a in alerts if a["level"] == "critical"]
        assert len(critical) >= 1
        assert critical[0]["trigger_type"] == "entry"

    def test_warning_alert(self, engine: ThoughtsEngine) -> None:
        """AMD at 170 → entry at 180 is ~5.9% away → warning."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={"AMD": 170.0}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        entry_alerts = [
            a for a in alerts if a["trigger_type"] == "entry"
        ]
        assert len(entry_alerts) == 1
        assert entry_alerts[0]["level"] == "warning"

    def test_watch_alert(self, engine: ThoughtsEngine) -> None:
        """AMD at 160 → entry at 180 is ~12.5% away → watch."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={"AMD": 160.0}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        entry_alerts = [
            a for a in alerts if a["trigger_type"] == "entry"
        ]
        assert len(entry_alerts) == 1
        assert entry_alerts[0]["level"] == "watch"

    def test_too_far_excluded(self, engine: ThoughtsEngine) -> None:
        """AMD at 165 → take_profit at 250 is ~51% away → excluded."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={"AMD": 165.0}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        tp_alerts = [
            a for a in alerts if a["trigger_type"] == "take_profit"
        ]
        assert len(tp_alerts) == 0

    def test_sorted_by_urgency(self, engine: ThoughtsEngine) -> None:
        """Critical alerts come first."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={"AMD": 178.0}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        if len(alerts) > 1:
            levels = [a["level"] for a in alerts]
            level_order = {"critical": 0, "warning": 1, "watch": 2}
            assert levels == sorted(levels, key=lambda lv: level_order[lv])

    def test_no_triggers(self, tmp_path: Path) -> None:
        """Empty DB returns empty list."""
        moves_db = tmp_path / "empty_moves.db"
        conn = sqlite3.connect(str(moves_db))
        conn.executescript("""
            CREATE TABLE watchlist_triggers (
                id INTEGER PRIMARY KEY, thesis_id INTEGER,
                symbol TEXT, trigger_type TEXT, condition TEXT,
                target_value REAL, notes TEXT, active INTEGER DEFAULT 1,
                created_at TEXT, triggered_at TEXT
            );
        """)
        conn.commit()
        conn.close()
        eng = ThoughtsEngine(
            thoughts_db=tmp_path / "thoughts.db", moves_db=moves_db
        )
        alerts = trigger_monitor.check_triggers(eng)
        assert alerts == []

    def test_no_prices(self, engine: ThoughtsEngine) -> None:
        """Returns empty if price fetch fails."""
        with patch.object(
            trigger_monitor, "_fetch_prices", return_value={}
        ):
            alerts = trigger_monitor.check_triggers(engine)
        assert alerts == []


class TestFormatAlerts:
    def test_formats_critical(self) -> None:
        alerts = [{
            "symbol": "AMD",
            "trigger_type": "entry",
            "target": 180.0,
            "current": 178.0,
            "pct_away": 1.1,
            "level": "critical",
            "trigger_id": 1,
            "thesis_id": 1,
            "notes": "Buy the dip",
        }]
        result = trigger_monitor.format_alerts(alerts)
        assert result is not None
        assert "⚠️" in result
        assert "AMD" in result
        assert "Buy the dip" in result

    def test_formats_warning(self) -> None:
        alerts = [{
            "symbol": "AMD",
            "trigger_type": "stop_loss",
            "target": 120.0,
            "current": 125.0,
            "pct_away": -4.0,
            "level": "warning",
            "trigger_id": 2,
            "thesis_id": 1,
            "notes": None,
        }]
        result = trigger_monitor.format_alerts(alerts)
        assert result is not None
        assert "👀" in result

    def test_watch_only_returns_none(self) -> None:
        alerts = [{
            "symbol": "AMD",
            "trigger_type": "entry",
            "target": 180.0,
            "current": 160.0,
            "pct_away": 12.5,
            "level": "watch",
            "trigger_id": 1,
            "thesis_id": 1,
            "notes": None,
        }]
        result = trigger_monitor.format_alerts(alerts)
        assert result is None

    def test_empty_alerts(self) -> None:
        assert trigger_monitor.format_alerts([]) is None
