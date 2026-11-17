"""`stock_compensations` table: a durable to-do list of stock releases that could not be completed because the
Product Catalog Service was unreachable while an order creation was being rolled back. Written ONLY in that failure
case; an administrator (or the automatic retry) works through it, and every entry is idempotent."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.connection import Base


class StockCompensation(Base):
    __tablename__ = "stock_compensations"
    __table_args__ = (
        UniqueConstraint("release_reference", name="uq_stock_compensations_release_reference"),
        CheckConstraint("status IN ('PENDING','DONE')", name="ck_stock_compensations_status"),
        CheckConstraint("quantity > 0", name="ck_stock_compensations_quantity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reserve_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    release_reference: Mapped[str] = mapped_column(String(100), nullable=False)
    reservation_confirmed: Mapped[bool] = mapped_column(nullable=False, default=True)  # False: the reserve call timed out, outcome unknown
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
