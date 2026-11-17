"""Order business logic.

Because the Order Service and the Product Catalog Service have separate databases, one database transaction cannot
cover both. Instead every stock change is a separate, IDEMPOTENT call (a unique `reference` per order line), and the
order is saved only after all stock was reserved:

  create:  validate user/address/products -> calculate on the backend -> reserve stock line by line
           -> save order + items in ONE local transaction
           -> if a reservation is refused, or saving fails: release what was reserved (compensation), nothing is saved
           -> if the release itself cannot reach the catalog: the pending release is stored in `stock_compensations`
              and retried (automatically and via the admin endpoint); the references make every retry safe.
  cancel:  mark the order CANCELLED (locked row) -> restore stock line by line; each line has a state
           (RESERVED -> RELEASED) and a fixed reference, so repeating the request can never restore stock twice.
"""
import logging
import math
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import settings
from app.core.security import AccessDeniedError, TokenData
from app.database.connection import commit
from app.models.order_item_model import STOCK_RELEASED, STOCK_RESERVED, OrderItem
from app.models.order_model import Order
from app.models.stock_compensation_model import StockCompensation
from app.schemas.order_schema import OrderCreate
from app.services import product_client, user_client
from app.services.errors import (ConflictError, NotFoundError, ProductNotFoundError, InsufficientStockError, ServiceError,
                                 ServiceUnavailableError, UnprocessableError)
from app.services.product_client import ProductInfo

logger = logging.getLogger("order-service")
CENT = Decimal("0.01")

# allowed order status workflow (anything else is rejected with 409)
TRANSITIONS: dict[str, set[str]] = {
    "PENDING": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"PROCESSING", "CANCELLED"},
    "PROCESSING": {"SHIPPED", "CANCELLED"},
    "SHIPPED": {"DELIVERED"},
    "DELIVERED": set(),
    "CANCELLED": set(),
}
CANCELLABLE = {"PENDING", "CONFIRMED", "PROCESSING"}  # SHIPPED and DELIVERED orders can no longer be cancelled


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def reserve_ref(order_number: str, product_id: int) -> str:
    return f"{order_number}:reserve:{product_id}"


def release_ref(order_number: str, product_id: int) -> str:
    return f"{order_number}:release:{product_id}"


# ============================================================================ price calculation (Decimal only)
@dataclass
class Line:
    product: ProductInfo
    quantity: int
    unit_price: Decimal
    discount: Decimal
    total: Decimal


def build_line(product: ProductInfo, quantity: int) -> Line:
    unit = money(product.price)
    discount = money((unit - money(product.discount_price)) * quantity) if product.discount_price is not None else Decimal("0.00")
    return Line(product, quantity, unit, discount, money(unit * quantity - discount))


def calculate_totals(lines: list[Line]) -> dict[str, Decimal]:
    """subtotal = sum(price x qty); discount = sum(item discounts); tax = rate x (subtotal - discount);
    shipping = flat rate unless (subtotal - discount) reaches the free-shipping threshold;
    total = subtotal - discount + tax + shipping."""
    subtotal = money(sum((l.unit_price * l.quantity for l in lines), Decimal("0")))
    discount = money(sum((l.discount for l in lines), Decimal("0")))
    taxable = subtotal - discount
    tax = money(taxable * settings.tax_rate)
    shipping = Decimal("0.00") if taxable >= settings.free_shipping_threshold else money(settings.shipping_flat_rate)
    total = money(subtotal - discount + tax + shipping)
    # validation: nothing negative, and the order total must match the saved items
    if min(subtotal, discount, tax, shipping, total) < 0 or discount > subtotal:
        raise UnprocessableError("The calculated order amounts are invalid")
    if sum((l.total for l in lines), Decimal("0")) != taxable or total != taxable + tax + shipping:
        raise UnprocessableError("The calculated order total does not match the order items")
    if total >= Decimal("10000000000"):
        raise UnprocessableError("The order total is too large")
    return {"subtotal": subtotal, "discount_amount": discount, "tax_amount": tax, "shipping_charge": shipping, "total_amount": total}


# ============================================================================ compensation (releasing reserved stock)
def _undo(order_number: str, product_id: int, quantity: int, confirmed: bool) -> None:
    """Give reserved stock back. If the reservation's outcome is unknown (the call timed out), repeat the reservation
    first: the same reference answers 'applied before' (200) or 'applied now' (201) or refuses (409), so we learn the truth
    and the stock always ends where it started."""
    if not confirmed:
        try:
            product_client.reserve_stock(product_id, quantity, reserve_ref(order_number, product_id), f"Order {order_number}")
        except (InsufficientStockError, ProductNotFoundError):
            return  # it was never applied: nothing to give back
    try:
        product_client.release_stock(product_id, quantity, release_ref(order_number, product_id), f"Released: order {order_number} was not completed")
    except ProductNotFoundError:
        logger.warning("Product %s no longer exists in the catalog; nothing to release for %s", product_id, order_number)


def _record_pending_release(db: Session, order_number: str, product_id: int, quantity: int, confirmed: bool, error: str) -> None:
    try:
        db.rollback()
        db.add(StockCompensation(order_number=order_number, product_id=product_id, quantity=quantity,
                                 reserve_reference=reserve_ref(order_number, product_id), release_reference=release_ref(order_number, product_id),
                                 reservation_confirmed=confirmed, status="PENDING", attempts=1, last_error=error[:255]))
        commit(db)
    except Exception:  # last resort: the database itself is failing
        db.rollback()
        logger.critical("COULD NOT RECORD PENDING STOCK RELEASE: order %s product %s quantity %s", order_number, product_id, quantity, exc_info=True)


def compensate(db: Session, order_number: str, entries: list[tuple[int, int, bool]]) -> int:
    """Release every (product_id, quantity, reservation_confirmed). What cannot be released now is stored for retry.
    Returns the number of entries left pending."""
    pending = 0
    for product_id, quantity, confirmed in entries:
        try:
            _undo(order_number, product_id, quantity, confirmed)
        except Exception as exc:
            pending += 1
            logger.error("Stock release for %s product %s deferred: %s", order_number, product_id, exc)
            _record_pending_release(db, order_number, product_id, quantity, confirmed, getattr(exc, "message", str(exc)))
    return pending


def list_compensations(db: Session) -> dict:
    rows = list(db.scalars(select(StockCompensation).order_by(StockCompensation.id.desc()).limit(200)))
    return {"items": rows, "pending": sum(1 for r in rows if r.status == "PENDING")}


def retry_compensations(db: Session) -> dict:
    rows = list(db.scalars(select(StockCompensation).where(StockCompensation.status == "PENDING").order_by(StockCompensation.id).limit(100)))
    done = 0
    for row in rows:
        row.attempts += 1
        try:
            _undo(row.order_number, row.product_id, row.quantity, row.reservation_confirmed)
            row.status, row.last_error = "DONE", None
            done += 1
        except Exception as exc:
            row.last_error = getattr(exc, "message", str(exc))[:255]
        commit(db)
    return {"processed": len(rows), "completed": done, "still_pending": len(rows) - done}


# ============================================================================ create
def _new_order_number(db: Session) -> str:
    for _ in range(10):
        number = f"{settings.order_number_prefix}-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(4).upper()}"
        if db.scalar(select(Order.id).where(Order.order_number == number)) is None:
            return number
    raise ServiceUnavailableError("Could not generate a unique order number. Please try again.")


def _shipping_snapshot(address: dict) -> dict:
    keys = ("id", "address_type", "full_name", "phone_number", "address_line_1", "address_line_2", "city", "state", "country", "postal_code")
    return {k: address.get(k) for k in keys}


def create_order(db: Session, token: TokenData, raw_token: str, payload: OrderCreate) -> Order:
    # 1. the customer: profile + the chosen delivery address, both from the User Management Service
    profile = user_client.get_profile(raw_token)
    if profile is None:
        raise UnprocessableError("A user profile is required before placing an order. Create your profile first.")
    if not profile.get("is_active", True):
        raise AccessDeniedError("Your profile is deactivated. Reactivate it before placing an order.")
    address = user_client.get_address(payload.shipping_address_id, raw_token)
    if address is None or address.get("user_id") != profile.get("id"):  # the User Service already hides other users' addresses
        raise UnprocessableError(f"Shipping address {payload.shipping_address_id} was not found among your saved addresses")

    # 2. the products: they must exist, be active and have enough stock (current prices come from the catalog)
    lines: list[Line] = []
    for item in payload.items:
        try:
            product = product_client.get_product(item.product_id)
        except ProductNotFoundError:
            raise UnprocessableError(f"Product {item.product_id} does not exist")
        if not product.is_active:
            raise UnprocessableError(f"Product {product.id} ({product.name}) is not available for ordering")
        if product.stock_quantity < item.quantity:
            raise ConflictError(f"Insufficient stock for {product.name}: {product.stock_quantity} available, {item.quantity} requested")
        lines.append(build_line(product, item.quantity))

    # 3. backend calculation (nothing the client could send is used)
    totals = calculate_totals(lines)

    # 4. reserve the stock, line by line, with a unique reference per line
    order_number = _new_order_number(db)
    reserved: list[tuple[int, int, bool]] = []
    for line in lines:
        try:
            product_client.reserve_stock(line.product.id, line.quantity, reserve_ref(order_number, line.product.id), f"Order {order_number}")
        except InsufficientStockError as exc:  # somebody else bought the stock in the meantime
            compensate(db, order_number, reserved)
            raise ConflictError(f"Insufficient stock for {line.product.name}: {exc.message.removeprefix('Insufficient stock: ')}")
        except ProductNotFoundError:
            compensate(db, order_number, reserved)
            raise UnprocessableError(f"Product {line.product.id} does not exist")
        except ServiceError:  # catalog unreachable: this line's outcome is unknown
            compensate(db, order_number, reserved + [(line.product.id, line.quantity, False)])
            raise
        reserved.append((line.product.id, line.quantity, True))

    # 5. save order + items in ONE local transaction; if that fails, give the stock back
    try:
        order = Order(order_number=order_number, user_id=token.user_id, shipping_address=_shipping_snapshot(address), notes=payload.notes,
                      order_status="PENDING", payment_status="PENDING", **totals)
        order.items = [OrderItem(product_id=l.product.id, product_name=l.product.name, sku=l.product.sku, unit_price=l.unit_price,
                                 quantity=l.quantity, discount_amount=l.discount, total_price=l.total, stock_status=STOCK_RESERVED) for l in lines]
        db.add(order)
        commit(db)
    except Exception:
        db.rollback()
        compensate(db, order_number, reserved)
        raise
    db.refresh(order)
    logger.info("Order %s created for user %s, total %s", order.order_number, token.user_id, order.total_amount)
    return order


# ============================================================================ read
def _order_query():
    return select(Order).options(selectinload(Order.items))


def _check_access(order: Order, token: TokenData) -> None:
    if order.user_id != token.user_id and not token.is_admin:
        raise AccessDeniedError("You do not have access to this order")


def get_order(db: Session, order_id: int, token: TokenData) -> Order:
    order = db.scalar(_order_query().where(Order.id == order_id))
    if order is None:
        raise NotFoundError(f"Order with id {order_id} not found")
    _check_access(order, token)
    return order


def get_order_by_number(db: Session, order_number: str, token: TokenData) -> Order:
    order = db.scalar(_order_query().where(Order.order_number == order_number.strip().upper()))
    if order is None:
        raise NotFoundError(f"Order {order_number} not found")
    _check_access(order, token)
    return order


def list_orders(db: Session, token: TokenData, status: str | None, date_from: date | None, date_to: date | None,
                user_id: int | None, page: int, page_size: int) -> dict:
    if date_from and date_to and date_from > date_to:
        raise UnprocessableError("date_from must not be after date_to")
    if token.is_admin:
        owner = user_id  # administrators: all orders, optionally one user's
    else:
        if user_id is not None and user_id != token.user_id:
            raise AccessDeniedError("You can only list your own orders")
        owner = token.user_id
    conditions = []
    if owner is not None:
        conditions.append(Order.user_id == owner)
    if status:
        conditions.append(Order.order_status == status)
    if date_from:
        conditions.append(Order.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        conditions.append(Order.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    total = db.scalar(select(func.count()).select_from(Order).where(*conditions))
    rows = list(db.scalars(_order_query().where(*conditions).order_by(Order.id.desc()).limit(page_size).offset((page - 1) * page_size)))
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "total_pages": math.ceil(total / page_size) if total else 0}


# ============================================================================ cancel and status
def cancel_order(db: Session, order_id: int, token: TokenData) -> tuple[Order, int]:
    """Idempotent cancellation. Returns (order, number of lines whose stock was restored by this call)."""
    # the row lock (MySQL) makes simultaneous cancel requests for one order run one after the other
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise NotFoundError(f"Order with id {order_id} not found")
    _check_access(order, token)
    if order.order_status != "CANCELLED":
        if order.order_status not in CANCELLABLE:
            db.rollback()
            raise ConflictError(f"Order cannot be cancelled: it is already {order.order_status}")
        order.order_status = "CANCELLED"
        commit(db)  # the order is closed first, so no other status change can happen while stock is restored
    return _release_items(db, order.id)


def _release_items(db: Session, order_id: int) -> tuple[Order, int]:
    order = db.scalar(_order_query().where(Order.id == order_id).execution_options(populate_existing=True))
    restored, failed = 0, 0
    for item in order.items:
        if item.stock_status != STOCK_RESERVED:
            continue  # already restored: never restore twice
        try:
            product_client.release_stock(item.product_id, item.quantity, release_ref(order.order_number, item.product_id),
                                         f"Released: order {order.order_number} cancelled")
        except ProductNotFoundError:
            logger.warning("Product %s no longer exists; marking its stock as released", item.product_id)
        except ServiceError as exc:
            failed += 1
            logger.error("Could not restore stock for order %s product %s: %s", order.order_number, item.product_id, exc.message)
            continue
        item.stock_status = STOCK_RELEASED
        commit(db)
        restored += 1
    if failed:
        raise ServiceUnavailableError(f"Order {order.order_number} is cancelled, but the stock of {failed} item(s) could not be restored yet "
                                      f"because the Product Catalog Service did not respond. Repeat the cancel request: stock that was "
                                      f"already restored will not be restored again.")
    db.refresh(order)
    return order, restored


def update_status(db: Session, order_id: int, new_status: str, token: TokenData) -> Order:
    order = db.scalar(_order_query().where(Order.id == order_id).with_for_update())
    if order is None:
        raise NotFoundError(f"Order with id {order_id} not found")
    if new_status == "CANCELLED":
        db.rollback()  # release the lock; cancellation takes its own
        return cancel_order(db, order_id, token)[0]
    if new_status == order.order_status:
        db.rollback()
        raise ConflictError(f"Order is already {new_status}")
    if new_status not in TRANSITIONS[order.order_status]:
        allowed = ", ".join(sorted(TRANSITIONS[order.order_status])) or "none (final status)"
        db.rollback()
        raise ConflictError(f"Invalid status change: {order.order_status} -> {new_status}. Allowed from {order.order_status}: {allowed}")
    order.order_status = new_status
    commit(db)
    db.refresh(order)
    return order


def get_items(db: Session, order_id: int, token: TokenData) -> Order:
    return get_order(db, order_id, token)
