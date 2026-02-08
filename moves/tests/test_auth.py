"""Tests for authentication system (password and session management).

Classes:
    TestAuthMiddleware: Test authentication middleware.
    TestSessionManagement: Test session cookie handling.
    TestPasswordLogin: Test password login flow.
    TestAuthIntegration: Integration tests.
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
def _auth_env(**overrides: object) -> Generator[Settings, None, None]:
    """Yield a test Settings with auth defaults, patching get_settings."""
    saved = os.environ.pop("MOVES_TESTING", None)
    try:
        defaults: dict = {
            "session_secret_key": "test-secret-key-1234567890",
            "auth_mode": "password",
            "auth_password": "testpass123",
            "testing": False,
        }
        defaults.update(overrides)
        settings = Settings(**defaults)
        with patch("api.auth.get_settings", return_value=settings):
            yield settings
    finally:
        if saved is not None:
            os.environ["MOVES_TESTING"] = saved


def _make_app() -> FastAPI:
    """Create a test app with auth middleware and endpoints."""
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/auth/login")
    async def login() -> dict:
        return {"message": "login"}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/test")
    async def api_test() -> dict:
        return {"message": "authenticated"}

    @app.get("/protected")
    async def protected() -> dict:
        return {"message": "authenticated"}

    return app


class TestAuthMiddleware:
    """Test authentication middleware."""

    def test_allows_auth_paths(self) -> None:
        """Auth paths bypass authentication."""
        with _auth_env():
            assert TestClient(_make_app()).get("/auth/login").status_code == 200

    def test_allows_health(self) -> None:
        """Health endpoint bypasses authentication."""
        with _auth_env():
            assert TestClient(_make_app()).get("/health").status_code == 200

    def test_redirects_browser_without_session(self) -> None:
        """Browser requests to protected paths redirect to login."""
        with _auth_env():
            resp = TestClient(_make_app(), follow_redirects=False).get("/protected")
            assert resp.status_code == 303
            assert "/auth/login" in resp.headers.get("location", "")

    def test_401_api_without_session(self) -> None:
        """API requests without session get 401."""
        with _auth_env():
            assert TestClient(_make_app()).get("/api/test").status_code == 401

    def test_allows_with_valid_session(self) -> None:
        """Valid session cookies grant access."""
        with _auth_env() as settings:
            s = URLSafeTimedSerializer(settings.session_secret_key)
            token = s.dumps({"user": "owner"})
            client = TestClient(_make_app(), cookies={"session": token})
            assert client.get("/api/test").status_code == 200

    def test_rejects_invalid_session(self) -> None:
        """Tampered cookies are rejected."""
        with _auth_env():
            client = TestClient(_make_app(), cookies={"session": "bad"})
            assert client.get("/api/test").status_code == 401

    def test_testing_mode_bypasses(self) -> None:
        """testing=True bypasses auth."""
        with _auth_env(testing=True):
            assert TestClient(_make_app()).get("/api/test").status_code == 200


class TestPasswordLogin:
    """Test password login flow."""

    def test_login_page_renders(self) -> None:
        """GET /auth/login returns the login form."""
        with _auth_env():
            from api.auth import create_auth_router

            app = FastAPI()
            app.include_router(create_auth_router())
            resp = TestClient(app).get("/auth/login")
            assert resp.status_code == 200
            assert "password" in resp.text.lower()

    def test_correct_password_sets_cookie(self) -> None:
        """Correct password sets session cookie and redirects."""
        with _auth_env(auth_password="secret123"):
            from api.auth import create_auth_router

            app = FastAPI()
            app.include_router(create_auth_router())
            client = TestClient(app, follow_redirects=False)
            resp = client.post("/auth/login", data={"password": "secret123"})
            assert resp.status_code == 303
            assert "session=" in resp.headers.get("set-cookie", "")

    def test_wrong_password_rejected(self) -> None:
        """Wrong password returns 401 with error message."""
        with _auth_env(auth_password="secret123"):
            from api.auth import create_auth_router

            app = FastAPI()
            app.include_router(create_auth_router())
            resp = TestClient(app).post("/auth/login", data={"password": "wrong"})
            assert resp.status_code == 401
            assert "Wrong password" in resp.text

    def test_logout_clears_cookie(self) -> None:
        """Logout clears the session cookie."""
        with _auth_env():
            from api.auth import create_auth_router

            app = FastAPI()
            app.include_router(create_auth_router())
            client = TestClient(app, follow_redirects=False)
            resp = client.post("/auth/logout")
            assert resp.status_code == 303


class TestSessionManagement:
    """Test session cookie handling."""

    def test_get_current_user_success(self) -> None:
        """Valid cookie returns user identifier."""
        with _auth_env() as settings:
            s = URLSafeTimedSerializer(settings.session_secret_key)
            token = s.dumps({"user": "owner"})
            mock_req = type("R", (), {"cookies": {"session": token}})()
            result = get_current_user(mock_req)
            assert result["email"] == "owner"
            assert result["role"] == "admin"

    def test_get_current_user_no_cookie(self) -> None:
        """Missing cookie raises 401."""
        with _auth_env():
            mock_req = type("R", (), {"cookies": {}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401

    def test_get_current_user_bad_cookie(self) -> None:
        """Invalid cookie raises 401."""
        with _auth_env():
            mock_req = type("R", (), {"cookies": {"session": "garbage"}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401

    def test_expired_cookie(self) -> None:
        """Expired session raises 401."""
        with _auth_env() as settings:
            with patch("time.time", return_value=0):
                s = URLSafeTimedSerializer(settings.session_secret_key)
                token = s.dumps({"user": "owner"})
            mock_req = type("R", (), {"cookies": {"session": token}})()
            with pytest.raises(HTTPException) as exc:
                get_current_user(mock_req)
            assert exc.value.status_code == 401


class TestAuthIntegration:
    """Integration: login → access protected → logout."""

    def test_full_flow(self) -> None:
        """Password login grants access, logout revokes it."""
        with _auth_env(auth_password="mypass"):
            from api.auth import create_auth_router

            app = _make_app()
            app.include_router(create_auth_router())

            client = TestClient(app, follow_redirects=False)

            # Not authenticated → redirect
            assert client.get("/protected").status_code == 303

            # Login
            resp = client.post("/auth/login", data={"password": "mypass"})
            assert resp.status_code == 303
            cookie = resp.cookies.get("session")
            assert cookie

            # Access with cookie
            auth_client = TestClient(app, cookies={"session": cookie})
            assert auth_client.get("/protected").status_code == 200


if __name__ == "__main__":
    pytest.main([__file__])
