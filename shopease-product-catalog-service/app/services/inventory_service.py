"""Inventory logic. Every stock change is ONE database transaction that updates the product and inserts the
history row together; both are committed or both are rolled back.

Concurrency: the stock is changed with a single relative UPDATE that carries the "never below zero" condition,
for example  UPDATE products SET stock_quantity = stock_quantity - :q WHERE id = :id AND stock_quantity - :q >= 0.
The database locks the row for the statement, so two simultaneous requests can never both read the same old
value (no lost updates) and the stock can never become negative."""
import math

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import commit
from app.models.inventory_model import InventoryTransaction
from app.models.product_model import Product
from app.schemas.product_schema import MAX_STOCK
from app.services.errors import ConflictError, NotFoundError


def _product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise NotFoundError(f"Product with id {product_id} not found")
    return product


def get_stock(db: Session, product_id: int) -> dict:
    product = _product_or_404(db, product_id)
    return {"product_id": product.id, "sku": product.sku, "name": product.name, "stock_quantity": product.stock_quantity,
            "low_stock_threshold": settings.low_stock_threshold, "is_low_stock": product.stock_quantity <= settings.low_stock_threshold}


def _replay(db: Session, reference: str, product_id: int, quantity: int, kind: str) -> InventoryTransaction | None:
    """Idempotency: a reference that was already used returns the original transaction and changes nothing.
    The same reference with a different product, type or quantity is a client error."""
    existing = db.scalar(select(InventoryTransaction).where(InventoryTransaction.reference == reference))
    if existing is None:
        return None
    if (existing.product_id, existing.transaction_type, existing.quantity) != (product_id, kind, quantity):
        raise ConflictError(f"Reference '{reference}' was already used for a different stock operation")
    existing.replayed = True  # not a column: tells the router to answer 200 instead of 201
    return existing


def change_stock(db: Session, product_id: int, quantity: int, kind: str, remarks: str | None, user_id: int,
                 reference: str | None = None) -> InventoryTransaction:
    delta = quantity if kind == "IN" else -quantity
    if reference:
        _product_or_404(db, product_id)
        replay = _replay(db, reference, product_id, quantity, kind)
        if replay is not None:
            return replay
    try:
        result = db.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock_quantity + delta >= 0, Product.stock_quantity + delta <= MAX_STOCK)
            .values(stock_quantity=Product.stock_quantity + delta))
        if result.rowcount == 0:  # nothing changed: not found, not enough stock, or above the maximum
            db.rollback()
            product = _product_or_404(db, product_id)
            if kind == "OUT":
                raise ConflictError(f"Insufficient stock: {product.stock_quantity} available, {quantity} requested")
            raise ConflictError(f"Stock cannot exceed {MAX_STOCK}")
        updated = db.scalar(select(Product.stock_quantity).where(Product.id == product_id))  # our own, locked row
        transaction = InventoryTransaction(product_id=product_id, transaction_type=kind, quantity=quantity,
                                           previous_stock=updated - delta, updated_stock=updated,
                                           remarks=remarks, performed_by=user_id, reference=reference)
        db.add(transaction)
        commit(db)  # stock update and history row are saved together
    except IntegrityError:
        db.rollback()
        if reference:  # two identical requests raced: the unique reference let only one of them through
            replay = _replay(db, reference, product_id, quantity, kind)
            if replay is not None:
                return replay
        raise
    except Exception:
        db.rollback()  # ... or neither of them
        raise
    db.refresh(transaction)
    return transaction


def history(db: Session, product_id: int, transaction_type: str | None, page: int, page_size: int) -> dict:
    _product_or_404(db, product_id)
    stmt = select(InventoryTransaction).where(InventoryTransaction.product_id == product_id)
    if transaction_type:
        stmt = stmt.where(InventoryTransaction.transaction_type == transaction_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(InventoryTransaction.id.desc()).limit(page_size).offset((page - 1) * page_size)))
    return {"product_id": product_id, "items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0}


def low_stock(db: Session, threshold: int | None, include_inactive: bool, page: int, page_size: int) -> dict:
    limit_value = settings.low_stock_threshold if threshold is None else threshold
    stmt = select(Product).where(Product.stock_quantity <= limit_value)
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(Product.stock_quantity, Product.id).limit(page_size).offset((page - 1) * page_size))
    items = [{"product_id": p.id, "sku": p.sku, "name": p.name, "category_id": p.category_id,
              "stock_quantity": p.stock_quantity, "is_active": p.is_active} for p in rows]
    return {"threshold": limit_value, "total": total, "page": page, "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0, "items": items}
