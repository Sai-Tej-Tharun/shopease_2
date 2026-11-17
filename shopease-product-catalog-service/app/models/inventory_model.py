"""`inventory_transactions` table: one row for every stock change (stock IN or OUT)."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base
from app.models.product_model import Product


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        CheckConstraint("transaction_type IN ('IN', 'OUT')", name="ck_inventory_type"),
        CheckConstraint("quantity > 0", name="ck_inventory_quantity_positive"),
        CheckConstraint("previous_stock >= 0 AND updated_stock >= 0", name="ck_inventory_stock_non_negative"),
        UniqueConstraint("reference", name="uq_inventory_reference"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_type: Mapped[str] = mapped_column(String(3), nullable=False)  # IN or OUT
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_stock: Mapped[int] = mapped_column(Integer, nullable=False)
    remarks: Mapped[str | None] = mapped_column(String(255), nullable=True)
    performed_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # authentication user id of the administrator
    # unique operation reference supplied by a calling service (for example "ORD-...:reserve:3"); makes retries idempotent
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="transactions")
