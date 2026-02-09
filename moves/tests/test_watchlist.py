"""Tests for watchlist trigger API endpoints.

Tests CRUD operations for watchlist triggers including creation,
listing, updating, toggling active status, and deletion.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from db.database import Database


@pytest.fixture
def client(seeded_db: Database) -> TestClient:
    """Create a test client with seeded database."""
    import api.deps as deps

    mock_container = MagicMock()
    mock_container.db = seeded_db
    mock_container.pricing = MagicMock()
    mock_container.thesis_engine = MagicMock()
    mock_container.signal_engine = MagicMock()
    mock_container.risk_manager = MagicMock()
    mock_container.principles_engine = MagicMock()
    mock_container.broker = MagicMock()

    deps._engines["container"] = mock_container
    app = create_app()
    return TestClient(app)


class TestWatchlistCRUD:
    """Test watchlist trigger CRUD operations."""

    def test_list_empty(self, client: TestClient) -> None:
        """Test listing triggers when none exist."""
        r = client.get("/api/fund/watchlist")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_trigger(self, client: TestClient) -> None:
        """Test creating a new watchlist trigger."""
        data = {
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
            "notes": "Buy zone",
        }
        r = client.post("/api/fund/watchlist", json=data)
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "AAPL"
        assert body["trigger_type"] == "entry"
        assert body["target_value"] == 150.0
        assert body["active"] == 1

    def test_create_trigger_with_thesis(
        self, client: TestClient
    ) -> None:
        """Test creating a trigger linked to a thesis."""
        data = {
            "thesis_id": 1,
            "symbol": "NVDA",
            "trigger_type": "stop_loss",
            "condition": "price_below",
            "target_value": 100.0,
        }
        r = client.post("/api/fund/watchlist", json=data)
        assert r.status_code == 200
        body = r.json()
        assert body["thesis_id"] == 1
        assert body["thesis_title"] is not None

    def test_create_trigger_invalid_thesis(
        self, client: TestClient
    ) -> None:
        """Test creating a trigger with nonexistent thesis."""
        data = {
            "thesis_id": 9999,
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_above",
            "target_value": 200.0,
        }
        r = client.post("/api/fund/watchlist", json=data)
        assert r.status_code == 404

    def test_create_trigger_invalid_type(
        self, client: TestClient
    ) -> None:
        """Test creating a trigger with invalid trigger_type."""
        data = {
            "symbol": "AAPL",
            "trigger_type": "invalid",
            "condition": "price_below",
            "target_value": 150.0,
        }
        r = client.post("/api/fund/watchlist", json=data)
        assert r.status_code == 422

    def test_list_triggers(self, client: TestClient) -> None:
        """Test listing triggers after creation."""
        client.post("/api/fund/watchlist", json={
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
        })
        client.post("/api/fund/watchlist", json={
            "symbol": "MSFT",
            "trigger_type": "take_profit",
            "condition": "price_above",
            "target_value": 500.0,
        })
        r = client.get("/api/fund/watchlist")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_update_trigger(self, client: TestClient) -> None:
        """Test updating a trigger's fields."""
        cr = client.post("/api/fund/watchlist", json={
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
        })
        tid = cr.json()["id"]
        r = client.put(
            f"/api/fund/watchlist/{tid}",
            json={"target_value": 145.0, "notes": "Updated"},
        )
        assert r.status_code == 200
        assert r.json()["target_value"] == 145.0
        assert r.json()["notes"] == "Updated"

    def test_toggle_active(self, client: TestClient) -> None:
        """Test toggling a trigger's active status."""
        cr = client.post("/api/fund/watchlist", json={
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
        })
        tid = cr.json()["id"]
        # Deactivate
        r = client.put(
            f"/api/fund/watchlist/{tid}", json={"active": 0}
        )
        assert r.status_code == 200
        assert r.json()["active"] == 0
        # Should not appear in active-only list
        lr = client.get("/api/fund/watchlist")
        assert len(lr.json()) == 0
        # Should appear in all list
        lr2 = client.get("/api/fund/watchlist?active_only=false")
        assert len(lr2.json()) == 1

    def test_delete_trigger(self, client: TestClient) -> None:
        """Test deleting a trigger."""
        cr = client.post("/api/fund/watchlist", json={
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
        })
        tid = cr.json()["id"]
        r = client.delete(f"/api/fund/watchlist/{tid}")
        assert r.status_code == 200
        assert r.json()["status"] == "deleted"
        # Verify gone
        lr = client.get("/api/fund/watchlist?active_only=false")
        assert len(lr.json()) == 0

    def test_delete_nonexistent(self, client: TestClient) -> None:
        """Test deleting a nonexistent trigger returns 404."""
        r = client.delete("/api/fund/watchlist/9999")
        assert r.status_code == 404

    def test_update_nonexistent(self, client: TestClient) -> None:
        """Test updating a nonexistent trigger returns 404."""
        r = client.put(
            "/api/fund/watchlist/9999",
            json={"target_value": 100.0},
        )
        assert r.status_code == 404

    def test_update_no_fields(self, client: TestClient) -> None:
        """Test updating with no fields returns 400."""
        cr = client.post("/api/fund/watchlist", json={
            "symbol": "AAPL",
            "trigger_type": "entry",
            "condition": "price_below",
            "target_value": 150.0,
        })
        tid = cr.json()["id"]
        r = client.put(f"/api/fund/watchlist/{tid}", json={})
        assert r.status_code == 400


class TestThesisEdit:
    """Test thesis inline editing via PATCH endpoint."""

    def test_patch_thesis_title(self, client: TestClient) -> None:
        """Test updating thesis title via PATCH."""
        r = client.patch(
            "/api/fund/theses/1",
            json={"title": "Updated Title"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated Title"

    def test_patch_thesis_conviction(
        self, client: TestClient
    ) -> None:
        """Test updating thesis conviction via PATCH."""
        r = client.patch(
            "/api/fund/theses/1", json={"conviction": 0.95}
        )
        assert r.status_code == 200
        assert r.json()["conviction"] == 0.95

    def test_patch_thesis_symbols(self, client: TestClient) -> None:
        """Test updating thesis symbols via PATCH."""
        r = client.patch(
            "/api/fund/theses/1",
            json={"symbols": ["AAPL", "GOOGL"]},
        )
        assert r.status_code == 200
        assert r.json()["symbols"] == ["AAPL", "GOOGL"]

    def test_patch_thesis_not_found(
        self, client: TestClient
    ) -> None:
        """Test PATCH on nonexistent thesis returns 404."""
        r = client.patch(
            "/api/fund/theses/9999", json={"title": "Nope"}
        )
        assert r.status_code == 404

    def test_patch_thesis_no_fields(
        self, client: TestClient
    ) -> None:
        """Test PATCH with no fields returns 400."""
        r = client.patch("/api/fund/theses/1", json={})
        assert r.status_code == 400
