"""Schemas for order items."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class OrderItemCreate(BaseModel):
    """Only the product id and the quantity are accepted: names, prices and totals always come from the catalog."""
    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    quantity: int = Field(...)

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Quantity must be at least 1")
        if v > settings.max_quantity_per_item:
            raise ValueError(f"Quantity must not exceed {settings.max_quantity_per_item} per item")
        return v


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    product_id: int
    product_name: str
    sku: str
    unit_price: Decimal
    quantity: int
    discount_amount: Decimal
    total_price: Decimal
    stock_status: str
    created_at: datetime


class OrderItemListResponse(BaseModel):
    order_id: int
    order_number: str
    items: list[OrderItemResponse]
    total_items: int
