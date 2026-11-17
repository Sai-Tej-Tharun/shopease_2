"""Business logic for addresses. Ownership is always checked against the profile of the JWT user."""
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.database.connection import commit
from app.models.address_model import UserAddress
from app.models.user_model import UserProfile
from app.schemas.address_schema import AddressCreate, AddressUpdate
from app.services.user_service import ServiceError


class AddressNotFoundError(ServiceError):
    status_code, error = 404, "Not Found"


class AddressForbiddenError(ServiceError):
    status_code, error = 403, "Forbidden"


def _lock_profile(db: Session, profile: UserProfile) -> None:
    """Serialises concurrent default-address changes of the same user (row lock on MySQL)."""
    db.execute(select(UserProfile.id).where(UserProfile.id == profile.id).with_for_update())


def _clear_default(db: Session, profile: UserProfile, except_id: int | None = None) -> None:
    stmt = update(UserAddress).where(UserAddress.user_id == profile.id, UserAddress.is_default.is_(True))
    if except_id is not None:
        stmt = stmt.where(UserAddress.id != except_id)
    db.execute(stmt.values(is_default=False))


def add_address(db: Session, profile: UserProfile, payload: AddressCreate) -> UserAddress:
    _lock_profile(db, profile)
    existing = db.scalar(select(func.count()).select_from(UserAddress).where(UserAddress.user_id == profile.id))
    make_default = payload.is_default or existing == 0  # the first address is always the default
    if make_default:
        _clear_default(db, profile)
    data = payload.model_dump()
    data["is_default"] = make_default
    address = UserAddress(user_id=profile.id, **data)
    db.add(address)
    commit(db)
    db.refresh(address)
    return address


def list_addresses(db: Session, profile: UserProfile) -> list[UserAddress]:
    return list(db.scalars(select(UserAddress).where(UserAddress.user_id == profile.id)
                           .order_by(UserAddress.is_default.desc(), UserAddress.id)))


def get_owned_address(db: Session, profile: UserProfile, address_id: int) -> UserAddress:
    address = db.get(UserAddress, address_id)
    if address is None:
        raise AddressNotFoundError(f"Address with id {address_id} not found")
    if address.user_id != profile.id:
        raise AddressForbiddenError("You do not have permission to access this address")
    return address


def update_address(db: Session, profile: UserProfile, address_id: int, payload: AddressUpdate) -> UserAddress:
    address = get_owned_address(db, profile, address_id)
    _lock_profile(db, profile)
    data = payload.model_dump()
    wants_default = data.pop("is_default")
    for field, value in data.items():
        setattr(address, field, value)
    if wants_default and not address.is_default:
        _clear_default(db, profile, except_id=address.id)
        address.is_default = True
    # is_default=false never removes the default: choose another default with PATCH .../{id}/default
    commit(db)
    db.refresh(address)
    return address


def set_default_address(db: Session, profile: UserProfile, address_id: int) -> UserAddress:
    address = get_owned_address(db, profile, address_id)
    _lock_profile(db, profile)
    _clear_default(db, profile, except_id=address.id)
    address.is_default = True
    commit(db)
    db.refresh(address)
    return address


def delete_address(db: Session, profile: UserProfile, address_id: int) -> None:
    address = get_owned_address(db, profile, address_id)
    _lock_profile(db, profile)
    was_default = address.is_default
    db.delete(address)
    db.flush()
    if was_default:  # keep exactly one default while addresses remain: promote the newest one
        newest = db.scalar(select(UserAddress).where(UserAddress.user_id == profile.id).order_by(UserAddress.id.desc()))
        if newest:
            newest.is_default = True
    commit(db)
