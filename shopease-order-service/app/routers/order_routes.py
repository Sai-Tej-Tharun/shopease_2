"""Order endpoints. The owner of an order is ALWAYS the user id inside the JWT, never a value from the request body."""
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import TokenData, bearer_scheme, get_token_data, require_admin
from app.database.connection import get_db
from app.schemas.order_schema import (CancelResponse, CompensationListResponse, CompensationRetryResponse, ErrorResponse, OrderCreate,
                                      OrderListResponse, OrderResponse, OrderStatus, StatusUpdate)
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Orders"])

AUTH = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
OWN = {**AUTH, 403: {"model": ErrorResponse, "description": "Order belongs to another user"}, 404: {"model": ErrorResponse, "description": "Order not found"}}


@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED, summary="Place an order",
             description="Send only product ids, quantities, a saved address id and optional notes. Prices, discounts, tax, shipping and totals are "
                         "calculated on the server from the current catalog prices. Stock is reserved in the Product Catalog Service.",
             responses={**AUTH, 403: {"model": ErrorResponse, "description": "Profile deactivated"},
                        409: {"model": ErrorResponse, "description": "Insufficient stock"},
                        422: {"model": ErrorResponse, "description": "Unknown or inactive product, invalid address, bad input"},
                        503: {"model": ErrorResponse, "description": "Product Catalog or User Service unavailable"}})
def create_order(payload: OrderCreate, credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
                 token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return order_service.create_order(db, token, credentials.credentials, payload)


@router.get("/", response_model=OrderListResponse, summary="List orders (customers: their own; administrators: all)", responses=AUTH)
def list_orders(order_status: OrderStatus | None = Query(None, alias="status", description="Filter by order status"),
                date_from: date | None = Query(None, description="Created on or after this date (YYYY-MM-DD)"),
                date_to: date | None = Query(None, description="Created on or before this date (YYYY-MM-DD)"),
                user_id: int | None = Query(None, ge=1, description="Administrators only: orders of one user"),
                page: int = Query(1, ge=1, le=1_000_000), page_size: int = Query(10, ge=1, le=50),
                token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return order_service.list_orders(db, token, order_status, date_from, date_to, user_id, page, page_size)


# NOTE: fixed paths are declared before /{order_id}
@router.get("/number/{order_number}", response_model=OrderResponse, summary="Get an order by its order number", responses=OWN)
def get_by_number(order_number: str, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return order_service.get_order_by_number(db, order_number, token)


@router.get("/compensations", response_model=CompensationListResponse, summary="Stock releases still waiting to be completed (admin)",
            responses={**AUTH, 403: {"model": ErrorResponse, "description": "Administrator role required"}})
def compensations(_: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return order_service.list_compensations(db)


@router.post("/compensations/retry", response_model=CompensationRetryResponse, summary="Retry pending stock releases (admin)",
             responses={**AUTH, 403: {"model": ErrorResponse, "description": "Administrator role required"}})
def retry_compensations(_: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return order_service.retry_compensations(db)


@router.get("/{order_id}", response_model=OrderResponse, summary="Get order details", responses=OWN)
def get_order(order_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return order_service.get_order(db, order_id, token)


@router.patch("/{order_id}/status", response_model=OrderResponse, summary="Change the order status (administrators)",
              description="Allowed changes: PENDING -> CONFIRMED | CANCELLED; CONFIRMED -> PROCESSING | CANCELLED; PROCESSING -> SHIPPED | CANCELLED; "
                          "SHIPPED -> DELIVERED. DELIVERED and CANCELLED are final. Changing to CANCELLED restores the reserved stock.",
              responses={**AUTH, 403: {"model": ErrorResponse, "description": "Administrator role required"}, 404: OWN[404],
                         409: {"model": ErrorResponse, "description": "Invalid status transition"}})
def change_status(order_id: int, payload: StatusUpdate, token: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return order_service.update_status(db, order_id, payload.status, token)


@router.post("/{order_id}/cancel", response_model=CancelResponse, summary="Cancel an order and restore its stock",
             description="Customers can cancel their own orders while they are PENDING, CONFIRMED or PROCESSING. Repeating the request is safe: "
                         "stock that was already restored is never restored again.",
             responses={**OWN, 409: {"model": ErrorResponse, "description": "Order is SHIPPED or DELIVERED"},
                        503: {"model": ErrorResponse, "description": "Cancelled, but stock restoration is still pending: repeat the request"}})
def cancel(order_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    order, restored = order_service.cancel_order(db, order_id, token)
    message = "Order cancelled and the reserved stock was restored" if restored else "Order is cancelled; no stock was restored again"
    return {"message": message, "stock_restored_now": restored, "order": order}
