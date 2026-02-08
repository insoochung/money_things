"""Authentication for the money_moves dashboard.

Supports two modes (controlled by MOVES_AUTH_MODE):
    - "password" (default): Simple username/password login via a form.
      Set MOVES_AUTH_PASSWORD in env. Username is ignored (single-user).
    - "google": Google OAuth2 with email allowlist (requires domain + HTTPS).

Session cookies are signed with itsdangerous and are httpOnly.

Functions:
    create_auth_router: Creates the FastAPI router with auth endpoints.
    get_current_user: Dependency to extract current user from session.

Classes:
    AuthMiddleware: Middleware to protect all routes except allowlisted ones.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import get_settings

logger = logging.getLogger(__name__)

SESSION_MAX_AGE = 7 * 24 * 60 * 60  # 7 days in seconds

_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Money Moves — Login</title>
<style>
  body{font-family:Inter,-apple-system,sans-serif;background:#f7f7f5;
       display:flex;justify-content:center;align-items:center;min-height:100vh;margin:0}
  .box{background:#fff;border:1px solid #e8e8e4;border-radius:3px;padding:2.5rem;
       width:320px;text-align:center}
  h1{font-size:1.3rem;font-weight:700;color:#37352f;margin:0 0 .25rem}
  .sub{font-size:.8rem;color:#9b9a97;margin-bottom:1.5rem}
  input{width:100%;padding:.6rem .75rem;border:1px solid #e8e8e4;border-radius:3px;
        font-family:inherit;font-size:.85rem;box-sizing:border-box;margin-bottom:.75rem}
  input:focus{outline:none;border-color:#37352f}
  button{width:100%;padding:.6rem;background:#37352f;color:#fff;border:none;
         border-radius:3px;font-family:inherit;font-size:.85rem;cursor:pointer}
  button:hover{background:#2f2d28}
  .err{color:#e03e3e;font-size:.8rem;margin-bottom:.75rem}
</style>
</head>
<body>
<form class="box" method="POST" action="/auth/login">
  <h1>Money Moves</h1>
  <p class="sub">Enter password to continue</p>
  {error}
  <input type="password" name="password" placeholder="Password" autofocus required>
  <button type="submit">Sign In</button>
</form>
</body></html>"""


def create_auth_router() -> APIRouter:
    """Create the auth router with login/logout endpoints.

    Supports password mode (default) and Google OAuth mode based on
    the MOVES_AUTH_MODE setting.

    Returns:
        FastAPI router with /auth/* endpoints.
    """
    settings = get_settings()
    router = APIRouter(prefix="/auth", tags=["authentication"])

    serializer = URLSafeTimedSerializer(settings.session_secret_key or "dev-secret-change-me")

    if settings.auth_mode == "google":
        _register_google_routes(router, serializer, settings)
    else:
        _register_password_routes(router, serializer, settings)

    @router.post("/logout")
    async def logout() -> Response:
        """Clear session cookie."""
        response = RedirectResponse("/auth/login", status_code=303)
        response.delete_cookie("session")
        return response

    return router


def _register_password_routes(
    router: APIRouter,
    serializer: URLSafeTimedSerializer,
    settings: Any,
) -> None:
    """Register simple password-based login routes.

    Args:
        router: The auth router to add routes to.
        serializer: Cookie serializer for session management.
        settings: Application settings with auth_password.
    """

    @router.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        """Serve the login form."""
        return HTMLResponse(_LOGIN_HTML.replace("{error}", ""))

    @router.post("/login")
    async def login_submit(request: Request) -> Response:
        """Validate password and set session cookie.

        Args:
            request: Form data with 'password' field.

        Returns:
            Redirect to dashboard on success, back to login on failure.
        """
        form = await request.form()
        password = form.get("password", "")

        expected = settings.auth_password
        if not expected:
            logger.error("MOVES_AUTH_PASSWORD not set — login disabled")
            return HTMLResponse(
                _LOGIN_HTML.replace(
                    "{error}", '<p class="err">Server misconfigured — no password set</p>'
                ),
                status_code=500,
            )

        if password != expected:
            client_host = request.client.host if request.client else "unknown"
            logger.warning("Failed login attempt from %s", client_host)
            return HTMLResponse(
                _LOGIN_HTML.replace("{error}", '<p class="err">Wrong password</p>'),
                status_code=401,
            )

        # Set session cookie
        session_data = serializer.dumps({"user": "owner"})
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            key="session",
            value=session_data,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=SESSION_MAX_AGE,
        )
        logger.info("Successful password login")
        return response


def _register_google_routes(
    router: APIRouter,
    serializer: URLSafeTimedSerializer,
    settings: Any,
) -> None:
    """Register Google OAuth login routes.

    Args:
        router: The auth router to add routes to.
        serializer: Cookie serializer for session management.
        settings: Application settings with Google OAuth config.
    """
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

    @router.get("/login")
    async def login(request: Request) -> RedirectResponse:
        """Redirect to Google OAuth consent screen."""
        return await oauth.google.authorize_redirect(request, settings.google_redirect_uri)

    @router.get("/callback")
    async def callback(request: Request) -> RedirectResponse:
        """Handle Google OAuth callback."""
        settings_now = get_settings()
        try:
            token = await oauth.google.authorize_access_token(request)
            user = await oauth.google.parse_id_token(request, token)
            email = user.get("email")

            if not email:
                return RedirectResponse("/auth/login", status_code=303)

            if email not in settings_now.allowed_emails:
                logger.warning("Unauthorized email: %s", email)
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized")

            session_data = serializer.dumps({"email": email})
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                key="session",
                value=session_data,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="lax",
                max_age=SESSION_MAX_AGE,
            )
            logger.info("Google OAuth login: %s", email)
            return response
        except Exception as e:
            logger.error("OAuth callback error: %s", e)
            return RedirectResponse("/auth/login", status_code=303)


def get_current_user(request: Request) -> dict:
    """Extract current user from session cookie.

    In testing mode (MOVES_TESTING=true), returns a default admin user so that
    tests can exercise all routes without setting up auth infrastructure.

    In production, reads user identity from the signed session cookie and looks
    up the user record in the database.

    Args:
        request: Request with session cookie.

    Returns:
        Dictionary with user info: id, email, name, role.

    Raises:
        HTTPException: If no valid session cookie is found.
    """
    settings = get_settings()

    # Testing mode bypass — return default admin user
    if settings.testing:
        return {"id": 1, "email": "insoo@default.local", "name": "Insoo", "role": "admin"}

    serializer = URLSafeTimedSerializer(settings.session_secret_key or "dev-secret-change-me")

    session_cookie = request.cookies.get("session")
    if not session_cookie:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No session")

    try:
        data = serializer.loads(session_cookie, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    # If session has user_id (multi-user mode), look up in DB
    user_id = data.get("user_id")
    if user_id:
        from api.deps import get_engines

        try:
            engines = get_engines()
            user_row = engines.db.fetchone(
                "SELECT id, email, name, role FROM users WHERE id = ? AND active = 1",
                (user_id,),
            )
            if not user_row:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
                )
            return dict(user_row)
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Engines not initialized"
            )

    # Legacy single-user session — return default user
    email = data.get("email") or data.get("user", "unknown")
    return {"id": 1, "email": email, "name": "Owner", "role": "admin"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect all routes except /auth/*, /health, /dashboard/*."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        settings = get_settings()
        self.serializer = URLSafeTimedSerializer(
            settings.session_secret_key or "dev-secret-change-me"
        )

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Validate session or redirect to login.

        Args:
            request: Incoming request.
            call_next: Next handler.

        Returns:
            Response from next handler, or redirect to /auth/login.
        """
        path = request.url.path

        # Allowlisted paths
        if path.startswith("/auth/") or path == "/health" or path.startswith("/dashboard/"):
            return await call_next(request)

        # Testing bypass
        settings = get_settings()
        if settings.testing:
            return await call_next(request)

        # Validate session
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            # API calls get 401, browser gets redirect
            if path.startswith("/api/") or path.startswith("/ws"):
                return Response("Authentication required", status_code=401)
            return RedirectResponse("/auth/login", status_code=303)

        try:
            self.serializer.loads(session_cookie, max_age=SESSION_MAX_AGE)
            return await call_next(request)
        except (BadSignature, SignatureExpired):
            if path.startswith("/api/") or path.startswith("/ws"):
                return Response("Session expired", status_code=401)
            return RedirectResponse("/auth/login", status_code=303)
