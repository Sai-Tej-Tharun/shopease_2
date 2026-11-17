"""Address endpoints. Every address is looked up through the profile of the JWT user."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.user_model import UserProfile
from app.schemas.address_schema import AddressCreate, AddressResponse, AddressUpdate
from app.schemas.user_schema import ErrorResponse, MessageResponse
from app.services import address_service, user_service

router = APIRouter(prefix="/api/users/addresses", tags=["User Addresses"])

COMMON = {
    401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"},
    403: {"model": ErrorResponse, "description": "Profile deactivated, or the address belongs to another user"},
    404: {"model": ErrorResponse, "description": "Profile or address not found"},
}


@router.post("", response_model=AddressResponse, status_code=status.HTTP_201_CREATED, summary="Add a new address",
             responses={**COMMON, 422: {"model": ErrorResponse, "description": "Validation error"}})
def add_address(payload: AddressCreate, profile: UserProfile = Depends(user_service.require_active_profile),
                db: Session = Depends(get_db)):
    return address_service.add_address(db, profile, payload)


@router.get("", response_model=list[AddressResponse], summary="List the logged-in user's addresses (default first)",
            responses=COMMON)
def list_addresses(profile: UserProfile = Depends(user_service.require_active_profile), db: Session = Depends(get_db)):
    return address_service.list_addresses(db, profile)


@router.get("/{address_id}", response_model=AddressResponse, summary="Get one address", responses=COMMON)
def get_address(address_id: int, profile: UserProfile = Depends(user_service.require_active_profile),
                db: Session = Depends(get_db)):
    return address_service.get_owned_address(db, profile, address_id)


@router.put("/{address_id}", response_model=AddressResponse, summary="Update an address",
            responses={**COMMON, 422: {"model": ErrorResponse, "description": "Validation error"}})
def update_address(address_id: int, payload: AddressUpdate,
                   profile: UserProfile = Depends(user_service.require_active_profile), db: Session = Depends(get_db)):
    return address_service.update_address(db, profile, address_id, payload)


@router.delete("/{address_id}", response_model=MessageResponse, summary="Delete an address", responses=COMMON)
def delete_address(address_id: int, profile: UserProfile = Depends(user_service.require_active_profile),
                   db: Session = Depends(get_db)):
    address_service.delete_address(db, profile, address_id)
    return MessageResponse(message="Address deleted successfully")


@router.patch("/{address_id}/default", response_model=AddressResponse,
              summary="Make this address the default (the previous default is cleared)", responses=COMMON)
def set_default(address_id: int, profile: UserProfile = Depends(user_service.require_active_profile),
                db: Session = Depends(get_db)):
    return address_service.set_default_address(db, profile, address_id)
