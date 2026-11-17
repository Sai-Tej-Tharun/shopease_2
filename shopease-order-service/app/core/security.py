"""JWT validation and role-based authorization. The service never issues login tokens and never sees passwords:
it only checks tokens created by the Authentication Service, using the shared secret from the environment.
The only token it creates is a short-lived SERVICE token for its calls to the Product Catalog Service."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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
    user_id: int  # claim "sub": the authentication user id
    role: str  # claim "role"
    expires_at: datetime

    @property
    def is_admin(self) -> bool:
        return self.role.strip().lower() == settings.admin_role.strip().lower()


def decode_access_token(token: str) -> TokenData:
    """Checks signature and expiry. Raises AuthenticationError for anything wrong."""
    try:
        claims = jwt.decode(token, settings.jwt_secret_key.get_secret_value(),
                            algorithms=[settings.jwt_algorithm],  # fixed list: "alg: none" tokens are rejected
                            options={"require": ["exp", "sub"]})
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


bearer_scheme = HTTPBearer(auto_error=False, description="Paste the access_token from the Authentication Service /api/auth/login")


def get_token_data(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> TokenData:
    """Dependency: any valid token (customer or administrator) -> 401 otherwise."""
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated: Bearer token is missing")
    data = decode_access_token(credentials.credentials)
    if settings.auth_verify_remote:
        verify_with_auth_service(credentials.credentials)
    return data


def require_admin(token: TokenData = Depends(get_token_data)) -> TokenData:
    """Dependency: valid token AND administrator role -> 401 / 403 otherwise."""
    if not token.is_admin:
        raise AccessDeniedError("Administrator access is required for this operation")
    return token


def create_service_token() -> str:
    """Short-lived token the Order Service uses for service-to-service calls to the Product Catalog Service
    (reading any product, reserving and releasing stock). It carries the administrator role of the shared
    secret scheme and a dedicated service-account id, so the Catalog history shows who made the change."""
    now = datetime.now(timezone.utc)
    claims = {"sub": str(settings.service_account_id), "role": settings.admin_role, "iat": now, "exp": now + timedelta(seconds=60)}
    return jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm)
