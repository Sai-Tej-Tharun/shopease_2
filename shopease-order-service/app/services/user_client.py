"""REST client for the User Management Service (HTTPX). The customer's OWN bearer token is forwarded, so the User
Service itself only ever answers with that customer's profile and addresses (another user's address id gives 404)."""
import logging
import time

import httpx

from app.core.config import settings
from app.core.security import AccessDeniedError, AuthenticationError
from app.services.errors import BadGatewayError, ServiceUnavailableError

logger = logging.getLogger("order-service.user-client")
TRANSPORT: httpx.BaseTransport | None = None  # the automated tests plug in a fake User Service here
UNAVAILABLE = "The User Management Service is unavailable. Please try again later."


def _get(path: str, token: str, forbidden_is_missing: bool = False) -> dict | None:
    """Returns the JSON body, or None when the resource does not exist (404)."""
    for attempt in range(1, settings.service_retry_attempts + 1):
        try:
            with httpx.Client(base_url=settings.user_service_url, timeout=settings.user_service_timeout, transport=TRANSPORT) as client:
                response = client.get(path, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            logger.warning("User Service GET %s failed (attempt %d/%d): %s", path, attempt, settings.service_retry_attempts, type(exc).__name__)
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code == 404 or (response.status_code == 403 and forbidden_is_missing):
                return None  # the User Service answers 403 for an address that belongs to someone else
            if response.status_code in (401, 403):
                try:
                    message = response.json().get("message", "Access refused by the User Management Service")
                except ValueError:
                    message = "Access refused by the User Management Service"
                raise (AuthenticationError if response.status_code == 401 else AccessDeniedError)(message)
            if response.status_code < 500:
                raise BadGatewayError(f"Unexpected answer from the User Management Service (HTTP {response.status_code})")
            logger.warning("User Service GET %s answered HTTP %d (attempt %d)", path, response.status_code, attempt)
        if attempt < settings.service_retry_attempts:
            time.sleep(0.1 * attempt)
    raise ServiceUnavailableError(UNAVAILABLE)


def get_profile(token: str) -> dict | None:
    """The logged-in user's profile (GET /api/users/profile) or None if the user has no profile yet."""
    return _get("/api/users/profile", token)


def get_address(address_id: int, token: str) -> dict | None:
    """One saved delivery address of the logged-in user (GET /api/users/addresses/{id}) or None."""
    return _get(f"/api/users/addresses/{address_id}", token, forbidden_is_missing=True)
