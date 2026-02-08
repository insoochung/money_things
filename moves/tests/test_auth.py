"""Tests for Google OAuth authentication system.

Tests session cookies, middleware auth enforcement, and user extraction.
OAuth callback tests (Google token exchange) are excluded — authlib's Starlette
integration requires actual request session state that's impractical to mock.

Classes:
    TestAuthMiddleware: Test authentication middleware.
    TestSessionManagement: Test session cookie handling.
    TestAuthEndpoints: Test auth endpoints (login, logout).
    TestAuthIntegration: Integration tests for the auth system.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from api.auth import AuthMiddleware, get_current_user
from config.settings import Settings


@contextmanager
def _auth_env(**setting_overrides: object) -> Generator[Settings, None, None]:
    """Context manager that yields a Settings with auth defaults.

    Temporarily unsets MOVES_TESTING so pydantic-settings doesn't override
    the constructor value, and patches get_settings for the middleware.

    Args:
        **setting_overrides: Any Settings fields to override.

    Yields:
        Configured Settings instance (patch on get_settings is active).
    """
    saved = os.environ.pop("MOVES_TESTING", None)
    try:
        defaults: dict = {
            "session_secret_key": "test-secret-key-for-testing-1234567890",
            "google_client_id": "test-client-id",
            "google_client_secret": "test-client-secret",
            "google_redirect_uri": "http://localhost:8000/auth/callback",
            "allowed_emails": ["test@example.com", "admin@example.com"],
            "testing": False,
        }
        defaults.update(setting_overrides)
        settings = Settings(**defaults)
        with patch("api.auth.get_settings", return_value=settings):
            yield settings
    finally:
        if saved is not None:
            os.environ["MOVES_TESTING"] = saved


def _make_app() -> FastAPI:
    """Create a minimal FastAPI app with auth middleware and test routes.

    Must be called inside an _auth_env() context so the middleware picks up
    the patched settings.

    Returns:
        FastAPI app with /auth/login, /health, and /protected endpoints.
    """
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/auth/login")
    async def login() -> dict:
        return {"message": "login page"}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    @app.get("/protected")
    async def protected() -> dict:
        return {"message": "authenticated"}

    return app


class TestAuthMiddleware:
    """Test authentication middleware functionality."""

    def test_allows_auth_paths(self) -> None:
        """Test that auth paths bypass authentication."""
        with _auth_env():
            client = TestClient(_make_app())
            assert client.get("/auth/login").status_code == 200

    def test_allows_health_endpoint(self) -> None:
        """Test that health endpoint bypasses authentication."""
        with _auth_env():
            client = TestClient(_make_app())
            assert client.get("/health").status_code == 200

    def test_blocks_protected_without_session(self) -> None:
        """Test that protected paths require a valid session cookie."""
        with _auth_env():
            client = TestClient(_make_app())
            assert client.get("/protected").status_code == 401

    def test_allows_protected_with_valid_session(self) -> None:
        """Test that valid session cookies grant access."""
        with _auth_env() as settings:
            serializer = URLSafeTimedSerializer(settings.session_secret_key)
            token = serializer.dumps({"email": "test@example.com"})
            client = TestClient(_make_app(), cookies={"session": token})
            resp = client.get("/protected")
            assert resp.status_code == 200
            assert resp.json()["message"] == "authenticated"

    def test_blocks_invalid_session(self) -> None:
        """Test that tampered session cookies are rejected."""
        with _auth_env():
            client = TestClient(_make_app(), cookies={"session": "tampered"})
            assert client.get("/protected").status_code == 401

    def test_blocks_wrong_secret_session(self) -> None:
        """Test that cookies signed with a different key are rejected."""
        with _auth_env():
            bad = URLSafeTimedSerializer("wrong-secret")
            token = bad.dumps({"email": "test@example.com"})
            client = TestClient(_make_app(), cookies={"session": token})
            assert client.get("/protected").status_code == 401

    def test_testing_mode_bypasses_auth(self) -> None:
        """Test that testing=True bypasses authentication entirely."""
        with _auth_env(testing=True):
            client = TestClient(_make_app())
            assert client.get("/protected").status_code == 200


class TestSessionManagement:
    """Test session cookie handling and user extraction."""

    def test_get_current_user_success(self) -> None:
        """Test extracting user email from a valid session cookie."""
        with _auth_env() as settings:
            serializer = URLSafeTimedSerializer(settings.session_secret_key)
            token = serializer.dumps({"email": "test@example.com"})

            mock_req = type("R", (), {"cookies": {"session": token}})()
            assert get_current_user(mock_req) == "test@example.com"

    def test_get_current_user_no_cookie(self) -> None:
        """Test that missing session cookie raises 401."""
        with _auth_env():
            mock_req = type("R", (), {"cookies": {}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401

    def test_get_current_user_invalid_cookie(self) -> None:
        """Test that invalid session cookie raises 401."""
        with _auth_env():
            mock_req = type("R", (), {"cookies": {"session": "garbage"}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401

    def test_get_current_user_expired_cookie(self) -> None:
        """Test that expired session cookie raises 401."""
        with _auth_env() as settings:
            with patch("time.time", return_value=0):
                serializer = URLSafeTimedSerializer(settings.session_secret_key)
                token = serializer.dumps({"email": "test@example.com"})

            mock_req = type("R", (), {"cookies": {"session": token}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401


class TestAuthEndpoints:
    """Test authentication endpoints (login, logout)."""

    def test_logout_clears_cookie(self) -> None:
        """Test that logout endpoint deletes the session cookie."""
        from api.auth import create_auth_router

        app = FastAPI()
        app.include_router(create_auth_router())
        client = TestClient(app)

        resp = client.post("/auth/logout")
        assert resp.status_code == 200
        assert "session=" in resp.headers.get("set-cookie", "")


class TestAuthIntegration:
    """Integration tests for the full authentication system."""

    def test_full_flow_unauthenticated_then_authenticated(self) -> None:
        """Test accessing protected content before and after getting a session."""
        with _auth_env() as settings:
            app = _make_app()

            # No session → 401
            assert TestClient(app).get("/protected").status_code == 401

            # Valid session → 200
            serializer = URLSafeTimedSerializer(settings.session_secret_key)
            token = serializer.dumps({"email": "test@example.com"})
            resp = TestClient(app, cookies={"session": token}).get("/protected")
            assert resp.status_code == 200
            assert resp.json()["message"] == "authenticated"


if __name__ == "__main__":
    pytest.main([__file__])
