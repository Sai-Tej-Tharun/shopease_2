"""Business logic for products: validation against categories, search, filtering, pagination, sorting."""
import math
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager

from app.database.connection import commit
from app.models.category_model import Category
from app.models.inventory_model import InventoryTransaction
from app.models.product_model import Product
from app.schemas.product_schema import ProductCreate, ProductPatch, ProductUpdate
from app.services.errors import ConflictError, NotFoundError, UnprocessableError

SORT_COLUMNS = {
    "id": lambda: Product.id,
    "name": lambda: Product.name,
    "price": lambda: func.coalesce(Product.discount_price, Product.price),  # the price the customer pays
    "stock_quantity": lambda: Product.stock_quantity,
    "created_at": lambda: Product.created_at,
}


def _check_category(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise UnprocessableError(f"Category with id {category_id} does not exist")
    if not category.is_active:
        raise UnprocessableError(f"Category '{category.name}' is inactive; activate it or choose another category")
    return category


def _duplicate_sku(db: Session, sku: str, exclude_id: int | None = None) -> bool:
    stmt = select(Product.id).where(Product.sku == sku)
    if exclude_id is not None:
        stmt = stmt.where(Product.id != exclude_id)
    return db.scalar(stmt) is not None


def _load(db: Session, product_id: int) -> Product:
    stmt = select(Product).join(Product.category).options(contains_eager(Product.category)).where(Product.id == product_id)
    # populate_existing: refresh the already-loaded object so a changed category_id shows the new category
    return db.scalar(stmt.execution_options(populate_existing=True))


def create_product(db: Session, payload: ProductCreate, user_id: int) -> Product:
    _check_category(db, payload.category_id)
    if _duplicate_sku(db, payload.sku):
        raise ConflictError(f"A product with SKU '{payload.sku}' already exists")
    product = Product(**payload.model_dump())
    db.add(product)
    try:
        db.flush()  # gets the id; product and its first stock record are saved together
        if product.stock_quantity > 0:
            db.add(InventoryTransaction(product_id=product.id, transaction_type="IN", quantity=product.stock_quantity,
                                        previous_stock=0, updated_stock=product.stock_quantity,
                                        remarks="Initial stock", performed_by=user_id))
        commit(db)
    except IntegrityError:
        db.rollback()
        raise ConflictError(f"A product with SKU '{payload.sku}' already exists")
    except Exception:
        db.rollback()
        raise
    return _load(db, product.id)


def get_product(db: Session, product_id: int, is_admin: bool) -> Product:
    product = _load(db, product_id)
    if product is None or (not is_admin and not (product.is_active and product.category.is_active)):
        raise NotFoundError(f"Product with id {product_id} not found")
    return product


def list_products(db: Session, *, is_admin: bool, search: str | None, category_id: int | None, min_price: Decimal | None,
                  max_price: Decimal | None, in_stock: bool | None, is_active: bool | None, sort_by: str, sort_order: str,
                  page: int, page_size: int) -> dict:
    if min_price is not None and max_price is not None and min_price > max_price:
        raise UnprocessableError("min_price must not be greater than max_price")
    effective_price = func.coalesce(Product.discount_price, Product.price)
    stmt = select(Product).join(Product.category)
    if not is_admin:  # customers never see inactive products (or products of inactive categories)
        stmt = stmt.where(Product.is_active.is_(True), Category.is_active.is_(True))
    elif is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))
    if search:
        escaped = search.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(or_(func.lower(Product.name).like(pattern, escape="\\"), func.lower(Product.sku).like(pattern, escape="\\")))
    if category_id is not None:
        stmt = stmt.where(Product.category_id == category_id)
    if min_price is not None:
        stmt = stmt.where(effective_price >= min_price)
    if max_price is not None:
        stmt = stmt.where(effective_price <= max_price)
    if in_stock is True:
        stmt = stmt.where(Product.stock_quantity > 0)
    elif in_stock is False:
        stmt = stmt.where(Product.stock_quantity == 0)

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    column = SORT_COLUMNS[sort_by]()
    order = column.desc() if sort_order == "desc" else column.asc()
    # only the requested page is read from the database; Product.id makes the order stable
    items = list(db.scalars(stmt.options(contains_eager(Product.category)).order_by(order, Product.id)
                            .limit(page_size).offset((page - 1) * page_size)))
    return {"items": items, "total": total, "page": page, "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0}


def _save(db: Session, product: Product) -> Product:
    try:
        commit(db)
    except IntegrityError:
        raise ConflictError(f"A product with SKU '{product.sku}' already exists")
    return _load(db, product.id)


def replace_product(db: Session, product_id: int, payload: ProductUpdate) -> Product:
    product = get_product(db, product_id, is_admin=True)
    if payload.category_id != product.category_id:
        _check_category(db, payload.category_id)
    if _duplicate_sku(db, payload.sku, exclude_id=product.id):
        raise ConflictError(f"A product with SKU '{payload.sku}' already exists")
    for field, value in payload.model_dump().items():
        setattr(product, field, value)
    return _save(db, product)


def patch_product(db: Session, product_id: int, payload: ProductPatch) -> Product:
    product = get_product(db, product_id, is_admin=True)
    changes = {f: getattr(payload, f) for f in payload.model_fields_set}
    if "category_id" in changes and changes["category_id"] != product.category_id:
        _check_category(db, changes["category_id"])
    if "sku" in changes and _duplicate_sku(db, changes["sku"], exclude_id=product.id):
        raise ConflictError(f"A product with SKU '{changes['sku']}' already exists")
    price = changes.get("price", product.price)
    discount = changes["discount_price"] if "discount_price" in changes else product.discount_price
    if discount is not None and discount >= price:  # checks the final state, not only the sent fields
        raise UnprocessableError("Discount price must be lower than the price")
    for field, value in changes.items():
        setattr(product, field, value)
    return _save(db, product)


def delete_product(db: Session, product_id: int) -> None:
    """Permanent delete (the inventory history of the product is removed with it). To keep the history,
    deactivate the product instead: PATCH {"is_active": false}."""
    product = get_product(db, product_id, is_admin=True)
    db.delete(product)
    commit(db)
