"""Pydantic schemas (validation rules) for the product APIs. Money uses Decimal with two decimals."""
import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, computed_field, field_validator, model_validator

from app.schemas.category_schema import clean_description, clean_name

_SKU_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{1,48}[A-Z0-9]$")
MAX_STOCK = 1_000_000_000
MAX_PRICE = Decimal("99999999.99")


def check_money(value: Decimal | None, label: str, allow_zero: bool = False) -> Decimal | None:
    if value is None:
        return None
    if not value.is_finite():
        raise ValueError(f"{label} must be a valid number")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    if value == 0 and not allow_zero:
        raise ValueError(f"{label} must be greater than 0")
    if value > MAX_PRICE:
        raise ValueError(f"{label} must not exceed {MAX_PRICE}")
    if value.as_tuple().exponent < -2:
        raise ValueError(f"{label} must have at most 2 decimal places")
    return value.quantize(Decimal("0.01"))


class ProductFields(BaseModel):
    """Rules shared by create / update / partial update. Unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _stock_is_not_editable_here(cls, data):
        if isinstance(data, dict) and "stock_quantity" in data and cls.__name__ != "ProductCreate":
            raise ValueError("Stock cannot be changed here; use the inventory stock-in and stock-out endpoints")
        return data

    @field_validator("category_id", check_fields=False)
    @classmethod
    def _category(cls, v):
        if v is not None and v < 1:
            raise ValueError("Category id must be a positive number")
        return v

    @field_validator("name", check_fields=False)
    @classmethod
    def _name(cls, v):
        return None if v is None else clean_name(v, "Product name", 150)

    @field_validator("description", check_fields=False)
    @classmethod
    def _description(cls, v):
        return clean_description(v)

    @field_validator("sku", check_fields=False)
    @classmethod
    def _sku(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if not _SKU_RE.match(v):
            raise ValueError("SKU must be 3 to 50 characters: letters, digits, . _ - (starting and ending with a letter or digit)")
        return v

    @field_validator("price", check_fields=False)
    @classmethod
    def _price(cls, v):
        return check_money(v, "Price")

    @field_validator("discount_price", check_fields=False)
    @classmethod
    def _discount(cls, v):
        return check_money(v, "Discount price", allow_zero=True)

    @field_validator("stock_quantity", check_fields=False)
    @classmethod
    def _stock(cls, v):
        if v is not None:
            if v < 0:
                raise ValueError("Stock quantity cannot be negative")
            if v > MAX_STOCK:
                raise ValueError(f"Stock quantity must not exceed {MAX_STOCK}")
        return v

    @field_validator("image_url", check_fields=False)
    @classmethod
    def _image(cls, v):
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if len(v) > 500 or any(ch.isspace() for ch in v) or not v.startswith(("http://", "https://")):
            raise ValueError("Image URL must be an http(s) URL of at most 500 characters without spaces")
        return v

    @field_validator("brand", check_fields=False)
    @classmethod
    def _brand(cls, v):
        if v is None:
            return None
        v = " ".join(v.split())
        if len(v) > 100:
            raise ValueError("Brand must be at most 100 characters")
        return v or None


class ProductCreate(ProductFields):
    category_id: int
    name: str
    description: str | None = None
    sku: str
    price: Decimal
    discount_price: Decimal | None = None
    stock_quantity: int = 0  # initial stock; recorded as the first inventory transaction
    image_url: str | None = None
    brand: str | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _discount_below_price(self):
        if self.discount_price is not None and self.discount_price >= self.price:
            raise ValueError("Discount price must be lower than the price")
        return self


class ProductUpdate(ProductFields):
    """PUT body: replaces every editable field (stock is changed only through the inventory APIs)."""

    category_id: int
    name: str
    description: str | None = None
    sku: str
    price: Decimal
    discount_price: Decimal | None = None
    image_url: str | None = None
    brand: str | None = None
    is_active: bool = True

    @model_validator(mode="after")
    def _discount_below_price(self):
        if self.discount_price is not None and self.discount_price >= self.price:
            raise ValueError("Discount price must be lower than the price")
        return self


class ProductPatch(ProductFields):
    """PATCH body: only the fields that are sent are changed."""

    category_id: int | None = None
    name: str | None = None
    description: str | None = None
    sku: str | None = None
    price: Decimal | None = None
    discount_price: Decimal | None = None
    image_url: str | None = None
    brand: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _check(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for required in ("category_id", "name", "sku", "price", "is_active"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        if self.price is not None and self.discount_price is not None and self.discount_price >= self.price:
            raise ValueError("Discount price must be lower than the price")
        return self


class CategorySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    category: CategorySummary
    name: str
    description: str | None
    sku: str
    price: Decimal
    discount_price: Decimal | None
    stock_quantity: int
    image_url: str | None
    brand: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def final_price(self) -> Decimal:
        """The price the customer pays: the discount price when there is one."""
        return self.discount_price if self.discount_price is not None else self.price

    @computed_field
    @property
    def in_stock(self) -> bool:
        return self.stock_quantity > 0


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
