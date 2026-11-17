"""Order item endpoints (read-only: an order's items are fixed when it is placed, which keeps totals consistent)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import TokenData, get_token_data
from app.database.connection import get_db
from app.schemas.order_item_schema import OrderItemListResponse, OrderItemResponse
from app.schemas.order_schema import ErrorResponse
from app.services import order_service
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/orders", tags=["Order Items"])

RESPONSES = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"},
             403: {"model": ErrorResponse, "description": "Order belongs to another user"},
             404: {"model": ErrorResponse, "description": "Order or item not found"}}


@router.get("/{order_id}/items", response_model=OrderItemListResponse, summary="List the items of an order (with price snapshots)", responses=RESPONSES)
def list_items(order_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    order = order_service.get_items(db, order_id, token)
    return {"order_id": order.id, "order_number": order.order_number, "items": order.items, "total_items": len(order.items)}


@router.get("/{order_id}/items/{item_id}", response_model=OrderItemResponse, summary="Get one item of an order", responses=RESPONSES)
def get_item(order_id: int, item_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    order = order_service.get_items(db, order_id, token)
    for item in order.items:
        if item.id == item_id:
            return item
    raise NotFoundError(f"Item {item_id} not found in order {order_id}")
