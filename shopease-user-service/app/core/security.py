"""JWT validation. The User Service never issues tokens and never sees passwords: it only checks tokens
that the Authentication Service created, using the shared secret from the environment."""
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings


class AuthenticationError(Exception):
    """Missing, invalid or expired token (HTTP 401)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AccessDeniedError(Exception):
    """Authenticated, but not allowed (HTTP 403)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AuthServiceUnavailableError(Exception):
    """The Authentication Service could not be reached while remote verification is enabled (HTTP 503)."""


@dataclass
class TokenData:
    user_id: int  # the authentication user id (claim "sub") – the ONLY source of identity
    role: str
    expires_at: datetime


def decode_access_token(token: str) -> TokenData:
    """Checks signature and expiry. Raises AuthenticationError for anything wrong."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # fixed list: "alg: none" tokens are rejected
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.PyJWTError:
        raise AuthenticationError("Invalid token")
    sub = str(claims["sub"])
    if not sub.isdigit() or int(sub) < 1:
        raise AuthenticationError("Invalid token")
    return TokenData(user_id=int(sub), role=str(claims.get("role", "")),
                     expires_at=datetime.fromtimestamp(claims["exp"], tz=timezone.utc))


def verify_with_auth_service(token: str) -> None:
    """Optional second check (AUTH_VERIFY_REMOTE=true): the Authentication Service re-validates the token
    and also checks that the account still exists and is active."""
    try:
        response = httpx.post(f"{settings.auth_service_url}/api/auth/verify-token",
                              headers={"Authorization": f"Bearer {token}"}, timeout=settings.auth_service_timeout)
    except httpx.HTTPError:
        raise AuthServiceUnavailableError()
    if response.status_code == 200:
        return
    try:
        message = response.json().get("message", "Token rejected by the Authentication Service")
    except ValueError:
        message = "Token rejected by the Authentication Service"
    if response.status_code == 401:
        raise AuthenticationError(message)
    if response.status_code == 403:
        raise AccessDeniedError(message)
    raise AuthServiceUnavailableError()


# auto_error=False -> we return our own consistent 401 instead of FastAPI's default
bearer_scheme = HTTPBearer(auto_error=False, description="Paste the access_token from the Authentication Service /api/auth/login")


def get_token_data(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> TokenData:
    """Dependency for every protected endpoint: validates the Bearer token and returns its claims."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated: Bearer token is missing")
    data = decode_access_token(credentials.credentials)
    if settings.auth_verify_remote:
        verify_with_auth_service(credentials.credentials)
    return data
