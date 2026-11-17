"""`orders` table. Money columns are DECIMAL; the shipping address is a JSON snapshot taken at order time."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.connection import Base

ORDER_STATUSES = ("PENDING", "CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED")
PAYMENT_STATUSES = ("PENDING", "PAID", "FAILED", "REFUNDED")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("order_number", name="uq_orders_order_number"),
        CheckConstraint("order_status IN ('PENDING','CONFIRMED','PROCESSING','SHIPPED','DELIVERED','CANCELLED')", name="ck_orders_status"),
        CheckConstraint("payment_status IN ('PENDING','PAID','FAILED','REFUNDED')", name="ck_orders_payment_status"),
        CheckConstraint("subtotal >= 0 AND discount_amount >= 0 AND shipping_charge >= 0 AND tax_amount >= 0 AND total_amount >= 0",
                        name="ck_orders_amounts_non_negative"),
        CheckConstraint("ABS(total_amount - (subtotal - discount_amount + tax_amount + shipping_charge)) < 0.005", name="ck_orders_total_matches"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # authentication user id (no local user copy)
    shipping_address: Mapped[dict] = mapped_column(JSON, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    shipping_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    order_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    payment_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan",
                                                     order_by="OrderItem.id", passive_deletes=True)


from app.models.order_item_model import OrderItem  # noqa: E402  (resolves the relationship string above)
