"""Business logic for profiles. Every function receives the authenticated user's id from the JWT;
no function accepts an owner id from a request body."""
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import TokenData, get_token_data
from app.database.connection import commit, get_db
from app.models.address_model import UserAddress
from app.models.user_model import UserProfile
from app.schemas.user_schema import ProfileCreate, ProfilePatch, ProfileUpdate


class ServiceError(Exception):
    status_code = 400
    error = "Bad Request"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ProfileNotFoundError(ServiceError):
    status_code, error = 404, "Not Found"


class DuplicateProfileError(ServiceError):
    status_code, error = 409, "Conflict"


class ProfileInactiveError(ServiceError):
    status_code, error = 403, "Forbidden"


class StateConflictError(ServiceError):
    status_code, error = 409, "Conflict"


def get_profile_by_auth_id(db: Session, auth_user_id: int) -> UserProfile | None:
    return db.scalar(select(UserProfile).where(UserProfile.auth_user_id == auth_user_id))


def create_profile(db: Session, auth_user_id: int, payload: ProfileCreate) -> UserProfile:
    if get_profile_by_auth_id(db, auth_user_id):
        raise DuplicateProfileError("A profile already exists for this user")
    profile = UserProfile(auth_user_id=auth_user_id, **payload.model_dump())
    db.add(profile)
    try:
        commit(db)
    except IntegrityError:  # two simultaneous requests: the unique constraint is the final guard
        raise DuplicateProfileError("A profile already exists for this user")
    db.refresh(profile)
    return profile


# ---------------------------------------------------------------- dependencies used by the routers
def require_profile(token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)) -> UserProfile:
    """The profile of the user in the JWT (404 if the user has not created one yet)."""
    profile = get_profile_by_auth_id(db, token.user_id)
    if profile is None:
        raise ProfileNotFoundError("Profile not found. Create your profile first (POST /api/users/profile)")
    return profile


def require_active_profile(profile: UserProfile = Depends(require_profile)) -> UserProfile:
    if not profile.is_active:
        raise ProfileInactiveError("Your profile is deactivated. Reactivate it (POST /api/users/profile/reactivate) to continue")
    return profile


# ---------------------------------------------------------------- profile operations
def replace_profile(db: Session, profile: UserProfile, payload: ProfileUpdate) -> UserProfile:
    for field, value in payload.model_dump().items():
        setattr(profile, field, value)
    commit(db)
    db.refresh(profile)
    return profile


def patch_profile(db: Session, profile: UserProfile, payload: ProfilePatch) -> UserProfile:
    for field in payload.model_fields_set:
        setattr(profile, field, getattr(payload, field))
    commit(db)
    db.refresh(profile)
    return profile


def delete_profile(db: Session, profile: UserProfile) -> None:
    db.delete(profile)  # the user's addresses are removed with it (ON DELETE CASCADE)
    commit(db)


def deactivate_profile(db: Session, profile: UserProfile) -> UserProfile:
    if not profile.is_active:
        raise StateConflictError("Profile is already deactivated")
    profile.is_active = False
    commit(db)
    db.refresh(profile)
    return profile


def reactivate_profile(db: Session, profile: UserProfile) -> UserProfile:
    if profile.is_active:
        raise StateConflictError("Profile is already active")
    profile.is_active = True
    commit(db)
    db.refresh(profile)
    return profile


def account_summary(db: Session, token: TokenData) -> dict:
    profile = get_profile_by_auth_id(db, token.user_id)
    summary = {"auth_user_id": token.user_id, "role": token.role, "has_profile": profile is not None,
               "profile_status": "none", "profile": profile, "address_count": 0, "default_address": None}
    if profile:
        summary["profile_status"] = "active" if profile.is_active else "deactivated"
        summary["address_count"] = db.scalar(select(func.count()).select_from(UserAddress).where(UserAddress.user_id == profile.id))
        summary["default_address"] = db.scalar(
            select(UserAddress).where(UserAddress.user_id == profile.id, UserAddress.is_default.is_(True)))
    return summary
