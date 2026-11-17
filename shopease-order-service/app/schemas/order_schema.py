"""Schemas for orders. The customer sends only items (product id + quantity), an address id and optional notes."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.schemas.order_item_schema import OrderItemCreate, OrderItemResponse

OrderStatus = Literal["PENDING", "CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"]


class OrderCreate(BaseModel):
    # extra="forbid": a user_id, prices, discounts, taxes or totals in the body are rejected, never trusted
    model_config = ConfigDict(extra="forbid")

    items: list[OrderItemCreate] = Field(..., min_length=1)
    shipping_address_id: int = Field(..., ge=1)
    notes: str | None = None

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        if len(v) > 500:
            raise ValueError("Notes must be at most 500 characters")
        return v or None

    @model_validator(mode="after")
    def _items(self):
        if len(self.items) > settings.max_items_per_order:
            raise ValueError(f"An order can contain at most {settings.max_items_per_order} different products")
        ids = [i.product_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product may appear only once; combine the quantities")
        return self


class StatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderStatus


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    user_id: int
    shipping_address: dict
    subtotal: Decimal
    discount_amount: Decimal
    shipping_charge: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    order_status: str
    payment_status: str
    notes: str | None
    items: list[OrderItemResponse]
    created_at: datetime
    updated_at: datetime


class OrderListResponse(BaseModel):
    items: list[OrderResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class CancelResponse(BaseModel):
    message: str
    stock_restored_now: int  # items whose stock was given back by THIS request (0 when it was done before)
    order: OrderResponse


class CompensationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    product_id: int
    quantity: int
    reservation_confirmed: bool
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class CompensationListResponse(BaseModel):
    items: list[CompensationResponse]
    pending: int


class CompensationRetryResponse(BaseModel):
    processed: int
    completed: int
    still_pending: int


class MessageResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    status_code: int
    error: str
    message: str
    details: list[dict] | None = None
