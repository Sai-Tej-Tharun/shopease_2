"""Product endpoints. Customers may read active products; only administrators may change anything."""
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import TokenData, get_token_data, require_admin
from app.database.connection import get_db
from app.schemas.inventory_schema import ErrorResponse, MessageResponse
from app.schemas.product_schema import ProductCreate, ProductListResponse, ProductPatch, ProductResponse, ProductUpdate
from app.services import product_service

router = APIRouter(prefix="/api/products", tags=["Products"])

E401 = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
E403 = {403: {"model": ErrorResponse, "description": "Administrator role required"}}
E404 = {404: {"model": ErrorResponse, "description": "Product not found"}}
E409 = {409: {"model": ErrorResponse, "description": "Duplicate SKU"}}
E422 = {422: {"model": ErrorResponse, "description": "Validation error (invalid category, negative price, ...)"}}


@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED, summary="Create a product (admin)",
             responses={**E401, **E403, **E409, **E422})
def create_product(payload: ProductCreate, token: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return product_service.create_product(db, payload, token.user_id)


@router.get("/", response_model=ProductListResponse,
            summary="List products with search, filters, pagination and sorting (customers: active products only)",
            responses={**E401, **E422})
def list_products(
    search: str | None = Query(None, min_length=1, max_length=100, description="Text in the product name or SKU"),
    category_id: int | None = Query(None, ge=1),
    min_price: Decimal | None = Query(None, ge=0, max_digits=10, decimal_places=2, description="Minimum selling price"),
    max_price: Decimal | None = Query(None, ge=0, max_digits=10, decimal_places=2, description="Maximum selling price"),
    in_stock: bool | None = Query(None, description="true: stock available, false: out of stock"),
    is_active: bool | None = Query(None, description="Administrators only: filter by status"),
    sort_by: Literal["id", "name", "price", "stock_quantity", "created_at"] = Query("id"),
    sort_order: Literal["asc", "desc"] = Query("asc"),
    page: int = Query(1, ge=1, le=1_000_000),
    page_size: int = Query(10, ge=1, le=100),
    token: TokenData = Depends(get_token_data), db: Session = Depends(get_db),
):
    return product_service.list_products(db, is_admin=token.is_admin, search=search, category_id=category_id, min_price=min_price,
                                         max_price=max_price, in_stock=in_stock, is_active=is_active, sort_by=sort_by,
                                         sort_order=sort_order, page=page, page_size=page_size)


@router.get("/{product_id}", response_model=ProductResponse, summary="Get product details", responses={**E401, **E404})
def get_product(product_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return product_service.get_product(db, product_id, token.is_admin)


@router.put("/{product_id}", response_model=ProductResponse, summary="Update a product (admin)",
            responses={**E401, **E403, **E404, **E409, **E422})
def update_product(product_id: int, payload: ProductUpdate, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return product_service.replace_product(db, product_id, payload)


@router.patch("/{product_id}", response_model=ProductResponse, summary="Partially update / activate / deactivate a product (admin)",
              responses={**E401, **E403, **E404, **E409, **E422})
def patch_product(product_id: int, payload: ProductPatch, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return product_service.patch_product(db, product_id, payload)


@router.delete("/{product_id}", response_model=MessageResponse, summary="Delete a product and its stock history (admin)",
               responses={**E401, **E403, **E404})
def delete_product(product_id: int, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    product_service.delete_product(db, product_id)
    return MessageResponse(message="Product deleted successfully")
