"""Inventory endpoints – administrators only."""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import TokenData, require_admin
from app.database.connection import get_db
from app.schemas.inventory_schema import (ErrorResponse, HistoryResponse, LowStockResponse, StockChange, StockResponse,
                                          TransactionResponse)
from app.services import inventory_service

router = APIRouter(prefix="/api/inventory", tags=["Inventory"])

COMMON = {
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"},
    403: {"model": ErrorResponse, "description": "Administrator role required"},
    404: {"model": ErrorResponse, "description": "Product not found"},
}


# NOTE: /low-stock is declared before /{product_id} so that it is never read as a product id
@router.get("/low-stock", response_model=LowStockResponse, summary="List products at or below the low-stock threshold",
            responses={401: COMMON[401], 403: COMMON[403]})
def low_stock(threshold: int | None = Query(None, ge=0, le=1_000_000, description=f"Default: LOW_STOCK_THRESHOLD ({settings.low_stock_threshold})"),
              include_inactive: bool = Query(False), page: int = Query(1, ge=1, le=1_000_000), page_size: int = Query(10, ge=1, le=100),
              _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return inventory_service.low_stock(db, threshold, include_inactive, page, page_size)


@router.get("/{product_id}", response_model=StockResponse, summary="Get the current stock of a product", responses=COMMON)
def get_stock(product_id: int, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return inventory_service.get_stock(db, product_id)


@router.post("/{product_id}/stock-in", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED,
             summary="Increase stock", responses={**COMMON, 409: {"model": ErrorResponse, "description": "Maximum stock exceeded"}})
def stock_in(product_id: int, payload: StockChange, response: Response, token: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    transaction = inventory_service.change_stock(db, product_id, payload.quantity, "IN", payload.remarks, token.user_id, payload.reference)
    if getattr(transaction, "replayed", False):
        response.status_code = status.HTTP_200_OK  # same reference seen before: nothing was changed again
    return transaction


@router.post("/{product_id}/stock-out", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED,
             summary="Decrease stock (never below zero)", responses={**COMMON, 409: {"model": ErrorResponse, "description": "Insufficient stock"}})
def stock_out(product_id: int, payload: StockChange, response: Response, token: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    transaction = inventory_service.change_stock(db, product_id, payload.quantity, "OUT", payload.remarks, token.user_id, payload.reference)
    if getattr(transaction, "replayed", False):
        response.status_code = status.HTTP_200_OK  # same reference seen before: nothing was changed again
    return transaction


@router.get("/{product_id}/history", response_model=HistoryResponse, summary="Stock transaction history (newest first)", responses=COMMON)
def stock_history(product_id: int, transaction_type: Literal["IN", "OUT"] | None = Query(None),
                  page: int = Query(1, ge=1, le=1_000_000), page_size: int = Query(10, ge=1, le=100),
                  _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return inventory_service.history(db, product_id, transaction_type, page, page_size)
