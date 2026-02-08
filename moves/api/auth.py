"""Google OAuth authentication for the money_moves dashboard.

This module provides session-based authentication using Google OAuth2 for the
money_moves web dashboard. Only users with email addresses in the ALLOWED_EMAILS
environment variable can access the system.

The authentication flow:
1. User visits /auth/login and is redirected to Google OAuth consent screen
2. Google redirects back to /auth/callback with an authorization code
3. We exchange the code for tokens and retrieve the user's email
4. If email is in the allowlist, we set a signed session cookie
5. All subsequent requests validate the session cookie via middleware

Session cookies are signed with a secret key and contain the user's email address.
The cookies are httpOnly and secure (when not in development) to prevent XSS attacks.

All API routes and WebSocket endpoints are protected except:
- /auth/* (login, callback, logout)
- /health (health check)

Functions:
    create_auth_router: Creates the FastAPI router with auth endpoints.
    get_current_user: Dependency to extract current user from session.
    auth_middleware: Middleware to protect all routes except allowlisted ones.

Dependencies:
    - authlib: OAuth client implementation
    - itsdangerous: Cookie signing and verification
"""

from __future__ import annotations

import logging
from typing import Any

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import get_settings

logger = logging.getLogger(__name__)


def create_auth_router() -> APIRouter:
    """Create the FastAPI router with Google OAuth authentication endpoints.

    Creates an OAuth client configured with Google's endpoints and registers
    three routes: login, callback, and logout.

    Returns:
        FastAPI router with /auth/login, /auth/callback, and /auth/logout endpoints.
    """
    settings = get_settings()
    router = APIRouter(prefix="/auth", tags=["authentication"])

    # Initialize OAuth client
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
        },
    )

    # Cookie serializer for session management
    serializer = URLSafeTimedSerializer(settings.session_secret_key)

    @router.get("/login")
    async def login(request: Request) -> RedirectResponse:
        """Initiate Google OAuth login flow.

        Redirects the user to Google's OAuth consent screen. After the user
        grants permission, Google will redirect back to /auth/callback.

        Args:
            request: FastAPI request object containing the OAuth client.

        Returns:
            Redirect response to Google OAuth consent screen.
        """
        redirect_uri = settings.google_redirect_uri
        return await oauth.google.authorize_redirect(request, redirect_uri)

    @router.get("/callback")
    async def callback(request: Request) -> RedirectResponse:
        """Handle Google OAuth callback and set session cookie.

        Exchanges the authorization code for access tokens, retrieves the user's
        email address, and verifies it against the allowlist. If authorized,
        sets a signed session cookie and redirects to the dashboard.

        Args:
            request: FastAPI request object containing the authorization code.

        Returns:
            Redirect response to dashboard (success) or login page (error).

        Raises:
            HTTPException: If the user's email is not in the allowlist.
        """
        settings = get_settings()

        try:
            # Exchange authorization code for tokens
            token = await oauth.google.authorize_access_token(request)

            # Get user info from Google
            user = await oauth.google.parse_id_token(request, token)
            email = user.get("email")

            if not email:
                logger.warning("No email found in Google token")
                return RedirectResponse("/auth/login", status_code=303)

            # Check if email is in allowlist
            if email not in settings.allowed_emails:
                logger.warning("Unauthorized email attempted login: %s", email)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=f"Email {email} not authorized"
                )

            # Create session cookie
            session_data = serializer.dumps({"email": email})

            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                key="session",
                value=session_data,
                httponly=True,
                secure=True,  # Only over HTTPS in production
                samesite="lax",
                max_age=7 * 24 * 60 * 60,  # 7 days
            )

            logger.info("Successful login for %s", email)
            return response

        except Exception as e:
            logger.error("OAuth callback error: %s", e)
            return RedirectResponse("/auth/login", status_code=303)

    @router.post("/logout")
    async def logout() -> Response:
        """Clear session cookie and log out user.

        Removes the session cookie by setting it to expire immediately.

        Returns:
            Response with cleared session cookie.
        """
        response = Response(status_code=200)
        response.delete_cookie("session")
        return response

    return router


def get_current_user(request: Request) -> str:
    """Extract current user email from session cookie.

    Dependency function that validates and deserializes the session cookie
    to extract the user's email address. Used to protect endpoints that
    require authentication.

    Args:
        request: FastAPI request object containing the session cookie.

    Returns:
        The authenticated user's email address.

    Raises:
        HTTPException: If no valid session cookie is found or it has expired.
    """
    settings = get_settings()
    serializer = URLSafeTimedSerializer(settings.session_secret_key)

    session_cookie = request.cookies.get("session")
    if not session_cookie:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No session cookie found"
        )

    try:
        # Deserialize and validate cookie (max age: 7 days)
        session_data = serializer.loads(session_cookie, max_age=7 * 24 * 60 * 60)
        return session_data["email"]
    except (BadSignature, SignatureExpired, KeyError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to protect all routes except authentication and health endpoints.

    Validates session cookies on all requests except for:
    - /auth/* (authentication flow)
    - /health (health check)
    - Static files

    If no valid session is found, returns 401 Unauthorized.
    """

    def __init__(self, app: Any) -> None:
        """Initialize the auth middleware.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)
        settings = get_settings()
        self.serializer = URLSafeTimedSerializer(settings.session_secret_key)

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        """Process request and validate session if required.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or route handler.

        Returns:
            The response from the next handler, or 401 if authentication fails.
        """
        path = request.url.path

        # Skip authentication for allowlisted paths
        if path.startswith("/auth/") or path == "/health" or path.startswith("/static/"):
            return await call_next(request)

        # Skip authentication in testing mode
        settings = get_settings()
        if settings.testing:
            return await call_next(request)

        # Validate session for all other paths
        session_cookie = request.cookies.get("session")
        if not session_cookie:
            return Response("Authentication required", status_code=401)

        try:
            # Validate session cookie
            self.serializer.loads(session_cookie, max_age=7 * 24 * 60 * 60)
            return await call_next(request)
        except (BadSignature, SignatureExpired):
            return Response("Session expired", status_code=401)
