"""Category endpoints. Customers may read active categories; only administrators may change anything."""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import TokenData, get_token_data, require_admin
from app.database.connection import get_db
from app.schemas.category_schema import CategoryCreate, CategoryResponse, CategoryUpdate
from app.schemas.inventory_schema import ErrorResponse, MessageResponse
from app.services import category_service

router = APIRouter(prefix="/api/categories", tags=["Categories"])

E401 = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
E403 = {403: {"model": ErrorResponse, "description": "Administrator role required"}}
E404 = {404: {"model": ErrorResponse, "description": "Category not found"}}
E409 = {409: {"model": ErrorResponse, "description": "Conflict (duplicate name / category still has products)"}}


@router.post("/", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED, summary="Create a category (admin)",
             responses={**E401, **E403, **E409})
def create_category(payload: CategoryCreate, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return category_service.create_category(db, payload)


@router.get("/", response_model=list[CategoryResponse], summary="List categories (customers: active only)", responses=E401)
def list_categories(is_active: bool | None = Query(None, description="Administrators only: filter by status"),
                    token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return category_service.list_categories(db, token.is_admin, is_active)


@router.get("/{category_id}", response_model=CategoryResponse, summary="Get category details", responses={**E401, **E404})
def get_category(category_id: int, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return category_service.get_category(db, category_id, token.is_admin)


@router.put("/{category_id}", response_model=CategoryResponse, summary="Update / activate / deactivate a category (admin)",
            responses={**E401, **E403, **E404, **E409})
def update_category(category_id: int, payload: CategoryUpdate, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    return category_service.update_category(db, category_id, payload)


@router.delete("/{category_id}", response_model=MessageResponse, summary="Delete a category without products (admin)",
               responses={**E401, **E403, **E404, **E409})
def delete_category(category_id: int, _: TokenData = Depends(require_admin), db: Session = Depends(get_db)):
    category_service.delete_category(db, category_id)
    return MessageResponse(message="Category deleted successfully")
