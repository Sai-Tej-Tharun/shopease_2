"""Business logic for categories."""
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import commit
from app.models.category_model import Category
from app.models.product_model import Product
from app.schemas.category_schema import CategoryCreate, CategoryUpdate
from app.services.errors import ConflictError, NotFoundError


def _find_by_name(db: Session, name: str, exclude_id: int | None = None) -> Category | None:
    stmt = select(Category).where(func.lower(Category.name) == name.lower())  # case-insensitive on every database
    if exclude_id is not None:
        stmt = stmt.where(Category.id != exclude_id)
    return db.scalar(stmt)


def create_category(db: Session, payload: CategoryCreate) -> Category:
    if _find_by_name(db, payload.name):
        raise ConflictError(f"A category named '{payload.name}' already exists")
    category = Category(**payload.model_dump())
    db.add(category)
    try:
        commit(db)
    except IntegrityError:
        raise ConflictError(f"A category named '{payload.name}' already exists")
    db.refresh(category)
    return category


def list_categories(db: Session, is_admin: bool, is_active: bool | None) -> list[Category]:
    stmt = select(Category).order_by(Category.name)
    if not is_admin:
        stmt = stmt.where(Category.is_active.is_(True))  # customers only see active categories
    elif is_active is not None:
        stmt = stmt.where(Category.is_active.is_(is_active))
    return list(db.scalars(stmt))


def get_category(db: Session, category_id: int, is_admin: bool) -> Category:
    category = db.get(Category, category_id)
    if category is None or (not is_admin and not category.is_active):
        raise NotFoundError(f"Category with id {category_id} not found")
    return category


def update_category(db: Session, category_id: int, payload: CategoryUpdate) -> Category:
    category = get_category(db, category_id, is_admin=True)
    if _find_by_name(db, payload.name, exclude_id=category.id):
        raise ConflictError(f"A category named '{payload.name}' already exists")
    for field, value in payload.model_dump().items():
        setattr(category, field, value)
    try:
        commit(db)
    except IntegrityError:
        raise ConflictError(f"A category named '{payload.name}' already exists")
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int) -> None:
    """Rule: a category that still contains products cannot be deleted (409). Move or delete its products
    first, or deactivate the category (PUT with is_active=false) to hide it from customers."""
    category = get_category(db, category_id, is_admin=True)
    product_count = db.scalar(select(func.count()).select_from(Product).where(Product.category_id == category.id))
    if product_count:
        raise ConflictError(f"Category '{category.name}' still contains {product_count} product(s). Reassign or delete them first, "
                            "or deactivate the category instead")
    db.delete(category)
    commit(db)
