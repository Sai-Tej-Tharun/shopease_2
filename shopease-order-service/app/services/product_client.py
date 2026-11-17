"""REST client for the Product Catalog Service (HTTPX). The Order Service never touches the catalog database.

* Reads (product details) and stock changes use the Order Service's short-lived SERVICE token.
* Stock changes carry a unique `reference`, so the Catalog applies each of them only once. That is what makes retrying
  after a timeout safe: a repeated reference answers 200 (already done) instead of changing the stock again.
* Timeouts, connection errors and 5xx answers are retried (`SERVICE_RETRY_ATTEMPTS`) and then reported as
  ServiceUnavailableError (HTTP 503)."""
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.core.config import settings
from app.core.security import create_service_token
from app.services.errors import BadGatewayError, InsufficientStockError, ProductNotFoundError, ServiceUnavailableError

logger = logging.getLogger("order-service.product-client")
TRANSPORT: httpx.BaseTransport | None = None  # the automated tests plug in a fake Product Catalog Service here
UNAVAILABLE = "The Product Catalog Service is unavailable. Please try again later."


@dataclass
class ProductInfo:
    id: int
    name: str
    sku: str
    price: Decimal
    discount_price: Decimal | None
    stock_quantity: int
    is_active: bool


def _send(method: str, path: str, json: dict | None = None) -> httpx.Response:
    """One call with retries. Returns the response for any answer below 500; raises ServiceUnavailableError otherwise."""
    last = "no answer"
    for attempt in range(1, settings.service_retry_attempts + 1):
        try:
            with httpx.Client(base_url=settings.product_service_url, timeout=settings.product_service_timeout, transport=TRANSPORT) as client:
                response = client.request(method, path, json=json, headers={"Authorization": f"Bearer {create_service_token()}"})
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        else:
            if response.status_code < 500:
                if response.status_code in (401, 403):
                    logger.error("Product Catalog Service rejected the service token (%s): check JWT_SECRET_KEY and ADMIN_ROLE", response.status_code)
                    raise BadGatewayError("The Product Catalog Service rejected the Order Service's credentials")
                return response
            last = f"HTTP {response.status_code}"
        logger.warning("Product Catalog Service %s %s failed (attempt %d/%d): %s", method, path, attempt, settings.service_retry_attempts, last)
        if attempt < settings.service_retry_attempts:
            time.sleep(0.1 * attempt)
    raise ServiceUnavailableError(UNAVAILABLE)


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", "request refused"))
    except ValueError:
        return "request refused"


def get_product(product_id: int) -> ProductInfo:
    """Current product data (price, stock, active flag). Raises ProductNotFoundError for an unknown id."""
    response = _send("GET", f"/api/products/{product_id}")
    if response.status_code == 404:
        raise ProductNotFoundError()
    if response.status_code != 200:
        raise BadGatewayError(f"Unexpected answer from the Product Catalog Service (HTTP {response.status_code})")
    data = response.json()
    return ProductInfo(id=data["id"], name=data["name"], sku=data["sku"], price=Decimal(str(data["price"])),
                       discount_price=None if data.get("discount_price") is None else Decimal(str(data["discount_price"])),
                       stock_quantity=int(data["stock_quantity"]), is_active=bool(data["is_active"]))


def _stock_change(action: str, product_id: int, quantity: int, reference: str, remarks: str) -> None:
    response = _send("POST", f"/api/inventory/{product_id}/{action}", json={"quantity": quantity, "remarks": remarks, "reference": reference})
    if response.status_code in (200, 201):  # 201 = applied now, 200 = this reference was already applied earlier
        return
    if response.status_code == 404:
        raise ProductNotFoundError()
    if response.status_code == 409 and action == "stock-out":
        raise InsufficientStockError(_message(response))
    raise BadGatewayError(f"The Product Catalog Service refused the stock change: {_message(response)}")


def reserve_stock(product_id: int, quantity: int, reference: str, remarks: str) -> None:
    """Deduct stock (stock-out). Safe to repeat with the same reference."""
    _stock_change("stock-out", product_id, quantity, reference, remarks)


def release_stock(product_id: int, quantity: int, reference: str, remarks: str) -> None:
    """Give stock back (stock-in). Safe to repeat with the same reference."""
    _stock_change("stock-in", product_id, quantity, reference, remarks)
