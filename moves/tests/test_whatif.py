"""Tests for the WhatIfEngine class."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from db.database import Database
from engine.whatif import WhatIfEngine


def _insert_signal(db: Database, symbol: str = "NVDA", action: str = "BUY") -> int:
    """Insert a test signal and return its id."""
    cursor = db.execute(
        "INSERT INTO signals (action, symbol, thesis_id, confidence, source, status) "
        "VALUES (?, ?, 1, 0.8, 'manual', 'rejected')",
        (action, symbol),
    )
    db.connect().commit()
    return cursor.lastrowid  # type: ignore[return-value]


class TestRecordPass:
    """Test recording rejected/ignored signals."""

    def test_record_rejected(self, seeded_db: Database) -> None:
        """Recording a rejected signal should create a what_if row."""
        engine = WhatIfEngine(seeded_db)
        sig_id = _insert_signal(seeded_db)
        engine.record_pass(sig_id, "rejected", 150.0)

        rows = seeded_db.fetchall("SELECT * FROM what_if")
        assert len(rows) == 1
        assert rows[0]["decision"] == "rejected"
        assert rows[0]["price_at_pass"] == 150.0

    def test_record_ignored(self, seeded_db: Database) -> None:
        """Recording an ignored signal should work."""
        engine = WhatIfEngine(seeded_db)
        sig_id = _insert_signal(seeded_db)
        engine.record_pass(sig_id, "ignored", 200.0)

        rows = seeded_db.fetchall("SELECT * FROM what_if")
        assert len(rows) == 1
        assert rows[0]["decision"] == "ignored"

    def test_invalid_decision_raises(self, seeded_db: Database) -> None:
        """Invalid decision type should raise ValueError."""
        engine = WhatIfEngine(seeded_db)
        with pytest.raises(ValueError, match="rejected.*ignored"):
            engine.record_pass(1, "approved", 100.0)


class TestUpdateAll:
    """Test price refresh and P/L computation."""

    def test_update_computes_pnl(self, seeded_db: Database) -> None:
        """update_all should compute hypothetical P/L for BUY signals."""
        engine = WhatIfEngine(seeded_db)
        sig_id = _insert_signal(seeded_db, "NVDA", "BUY")
        engine.record_pass(sig_id, "rejected", 100.0)

        with patch("engine.whatif.get_price", return_value={"price": 120.0}):
            updated = engine.update_all()

        assert updated == 1
        row = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (sig_id,))
        assert row["current_price"] == 120.0
        assert row["hypothetical_pnl"] == 20.0  # 120 - 100
        assert row["hypothetical_pnl_pct"] == pytest.approx(0.2)

    def test_update_sell_signal(self, seeded_db: Database) -> None:
        """SELL signal P/L should be inverted (profit if price drops)."""
        engine = WhatIfEngine(seeded_db)
        sig_id = _insert_signal(seeded_db, "NVDA", "SELL")
        engine.record_pass(sig_id, "rejected", 100.0)

        with patch("engine.whatif.get_price", return_value={"price": 80.0}):
            engine.update_all()

        row = seeded_db.fetchone("SELECT * FROM what_if WHERE signal_id = ?", (sig_id,))
        assert row["hypothetical_pnl"] == 20.0  # 100 - 80
        assert row["hypothetical_pnl_pct"] == pytest.approx(0.2)

    def test_update_handles_price_failure(self, seeded_db: Database) -> None:
        """Price fetch failure should not crash update_all."""
        engine = WhatIfEngine(seeded_db)
        sig_id = _insert_signal(seeded_db, "FAKE")
        engine.record_pass(sig_id, "ignored", 50.0)

        with patch("engine.whatif.get_price", side_effect=RuntimeError("no price")):
            updated = engine.update_all()

        assert updated == 0


class TestSummary:
    """Test summary statistics."""

    def test_empty_summary(self, seeded_db: Database) -> None:
        """Empty what_if returns zero summary."""
        engine = WhatIfEngine(seeded_db)
        summary = engine.get_summary()
        assert summary["total_tracked"] == 0
        assert summary["pass_accuracy"] == 0.0

    def test_summary_with_data(self, seeded_db: Database) -> None:
        """Summary should compute meaningful metrics."""
        engine = WhatIfEngine(seeded_db)

        # Rejected signal that would have lost money (correct rejection)
        sig1 = _insert_signal(seeded_db, "NVDA", "BUY")
        engine.record_pass(sig1, "rejected", 100.0)
        seeded_db.execute(
            "UPDATE what_if SET hypothetical_pnl = -10, hypothetical_pnl_pct = -0.1 "
            "WHERE signal_id = ?",
            (sig1,),
        )

        # Ignored signal that would have made money (missed opportunity)
        sig2 = _insert_signal(seeded_db, "AVGO", "BUY")
        engine.record_pass(sig2, "ignored", 50.0)
        seeded_db.execute(
            "UPDATE what_if SET hypothetical_pnl = 20, hypothetical_pnl_pct = 0.4 "
            "WHERE signal_id = ?",
            (sig2,),
        )
        seeded_db.connect().commit()

        summary = engine.get_summary()
        assert summary["total_tracked"] == 2
        assert summary["reject_accuracy"] == 1.0  # 1/1 correct rejection
        assert summary["ignore_cost"] == pytest.approx(0.4)  # missed 40% gain


class TestListWhatifs:
    """Test listing what-if records."""

    def test_list_all(self, seeded_db: Database) -> None:
        """list_whatifs with no filter returns all records."""
        engine = WhatIfEngine(seeded_db)
        sig1 = _insert_signal(seeded_db, "NVDA")
        sig2 = _insert_signal(seeded_db, "AVGO")
        engine.record_pass(sig1, "rejected", 100.0)
        engine.record_pass(sig2, "ignored", 50.0)

        results = engine.list_whatifs()
        assert len(results) == 2

    def test_list_filtered(self, seeded_db: Database) -> None:
        """list_whatifs with filter returns only matching records."""
        engine = WhatIfEngine(seeded_db)
        sig1 = _insert_signal(seeded_db, "NVDA")
        sig2 = _insert_signal(seeded_db, "AVGO")
        engine.record_pass(sig1, "rejected", 100.0)
        engine.record_pass(sig2, "ignored", 50.0)

        rejected = engine.list_whatifs(decision="rejected")
        assert len(rejected) == 1
        assert rejected[0]["decision"] == "rejected"


class TestHypotheticalPnl:
    """Test P/L computation logic."""

    def test_buy_profit(self) -> None:
        """BUY signal with price increase = profit."""
        pnl, pct = WhatIfEngine._compute_hypothetical_pnl("BUY", 100, 120)
        assert pnl == 20
        assert pct == pytest.approx(0.2)

    def test_buy_loss(self) -> None:
        """BUY signal with price decrease = loss."""
        pnl, pct = WhatIfEngine._compute_hypothetical_pnl("BUY", 100, 80)
        assert pnl == -20
        assert pct == pytest.approx(-0.2)

    def test_short_profit(self) -> None:
        """SHORT signal with price decrease = profit."""
        pnl, pct = WhatIfEngine._compute_hypothetical_pnl("SHORT", 100, 80)
        assert pnl == 20
        assert pct == pytest.approx(0.2)
