"""Tests for the approval workflow."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from engine import Signal, SignalAction
from engine.approval import ApprovalWorkflow


@pytest.fixture
def mock_db():
    """Create a mock database."""
    db = MagicMock()
    db.execute = MagicMock()
    db.fetch_one = MagicMock(return_value=None)
    db.fetch_all = MagicMock(return_value=[])
    return db


@pytest.fixture
def workflow(mock_db):
    """Create an ApprovalWorkflow with mocks."""
    return ApprovalWorkflow(
        db=mock_db,
        signal_engine=MagicMock(),
        broker=MagicMock(),
        risk_manager=MagicMock(),
    )


class TestAutoApproveRules:
    """Tests for auto-approve logic."""

    def test_low_value_auto_approve(self, workflow, mock_db):
        """Trades below $500 are auto-approved."""
        mock_db.fetch_one.side_effect = lambda q, p=None: (
            {"total_value": 100000} if "portfolio_value" in q else None
        )

        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="AAPL",
            size_pct=0.3,  # 0.3% of $100k = $300 < $500
            confidence=0.5,
        )
        assert workflow.should_auto_approve(signal) is True

    def test_high_value_not_auto_approved(self, workflow, mock_db):
        """Trades above $500 are not auto-approved (unless other rules match)."""
        mock_db.fetch_one.side_effect = lambda q, p=None: (
            {"total_value": 100000} if "portfolio_value" in q else None
        )

        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="AAPL",
            size_pct=5.0,  # 5% of $100k = $5000 > $500
            confidence=0.5,
        )
        assert workflow.should_auto_approve(signal) is False

    def test_high_confidence_confirmed_thesis(self, workflow, mock_db):
        """High confidence + confirmed thesis triggers auto-approve."""
        mock_db.fetch_one.side_effect = lambda q, p=None: (
            {"status": "confirmed"} if "theses" in q else None
        )

        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="NVDA",
            thesis_id=1,
            confidence=0.95,
        )
        assert workflow.should_auto_approve(signal) is True

    def test_high_confidence_unconfirmed_thesis(self, workflow, mock_db):
        """High confidence but unconfirmed thesis doesn't auto-approve."""
        mock_db.fetch_one.side_effect = lambda q, p=None: (
            {"status": "active"} if "theses" in q else None
        )

        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="NVDA",
            thesis_id=1,
            confidence=0.95,
        )
        assert workflow.should_auto_approve(signal) is False

    def test_rebalance_auto_approve(self, workflow):
        """Rebalance signals are auto-approved."""
        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="VTI",
            confidence=0.5,
        )
        # Manually set source to "rebalance" (not in SignalSource enum yet)
        object.__setattr__(signal, "source", "rebalance")
        assert workflow.should_auto_approve(signal) is True

    def test_no_rules_match(self, workflow):
        """Signal with no matching rules is not auto-approved."""
        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="AAPL",
            confidence=0.5,
        )
        assert workflow.should_auto_approve(signal) is False


class TestProcessSignal:
    """Tests for signal processing."""

    def test_auto_approved_signal(self, workflow, mock_db):
        """Auto-approved signal updates status."""
        mock_db.fetch_one.side_effect = lambda q, p=None: (
            {"total_value": 100000} if "portfolio_value" in q else None
        )

        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="AAPL",
            size_pct=0.1,
            confidence=0.5,
        )
        result = workflow.process_signal(signal)

        assert result["status"] == "auto_approved"
        mock_db.execute.assert_called()

    def test_pending_signal(self, workflow):
        """Non-auto-approved signal stays pending."""
        signal = Signal(
            id=1,
            action=SignalAction.BUY,
            symbol="AAPL",
            confidence=0.5,
        )
        result = workflow.process_signal(signal)

        assert result["status"] == "pending"


class TestModifySignal:
    """Tests for signal modification."""

    def test_modify_size(self, workflow, mock_db):
        """Size can be modified on pending signal."""
        mock_db.fetch_one.return_value = {
            "id": 1,
            "status": "pending",
            "symbol": "AAPL",
        }

        result = workflow.modify_signal(1, size_override=2.0)

        assert result["success"] is True
        mock_db.execute.assert_called()

    def test_modify_nonexistent(self, workflow, mock_db):
        """Modifying nonexistent signal fails."""
        mock_db.fetch_one.return_value = None

        result = workflow.modify_signal(999)
        assert result["success"] is False

    def test_modify_non_pending(self, workflow, mock_db):
        """Cannot modify approved signal."""
        mock_db.fetch_one.return_value = {
            "id": 1,
            "status": "approved",
            "symbol": "AAPL",
        }

        result = workflow.modify_signal(1, size_override=2.0)
        assert result["success"] is False

    def test_modify_no_changes(self, workflow, mock_db):
        """No modifications specified returns failure."""
        mock_db.fetch_one.return_value = {
            "id": 1,
            "status": "pending",
            "symbol": "AAPL",
        }

        result = workflow.modify_signal(1)
        assert result["success"] is False
