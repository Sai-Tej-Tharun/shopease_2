"""`order_items` table: product name, SKU and price are SNAPSHOTS, so old orders stay correct when the catalog changes."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base

STOCK_RESERVED, STOCK_RELEASED = "RESERVED", "RELEASED"


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_order_items_quantity_positive"),
        CheckConstraint("unit_price >= 0 AND discount_amount >= 0 AND total_price >= 0", name="ck_order_items_amounts_non_negative"),
        CheckConstraint("ABS(total_price - (unit_price * quantity - discount_amount)) < 0.005", name="ck_order_items_total_matches"),
        CheckConstraint("stock_status IN ('RESERVED','RELEASED')", name="ck_order_items_stock_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # id in the Product Catalog Service (no local copy)
    product_name: Mapped[str] = mapped_column(String(150), nullable=False)
    sku: Mapped[str] = mapped_column(String(50), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # inventory workflow state of this line: RESERVED (stock deducted in the catalog) -> RELEASED (restored once)
    stock_status: Mapped[str] = mapped_column(String(20), nullable=False, default=STOCK_RESERVED)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    order: Mapped["Order"] = relationship(back_populates="items")


from app.models.order_model import Order  # noqa: E402
