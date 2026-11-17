"""Profile and account endpoints. The user is always identified by the JWT, never by the request body."""
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.security import TokenData, get_token_data
from app.database.connection import get_db
from app.models.user_model import UserProfile
from app.schemas.user_schema import (AccountResponse, ErrorResponse, MessageResponse, ProfileCreate, ProfilePatch,
                                     ProfileResponse, ProfileUpdate)
from app.services import user_service

router = APIRouter(prefix="/api/users", tags=["User Profile"])

E401 = {401: {"model": ErrorResponse, "description": "Missing, invalid or expired token"}}
E403 = {403: {"model": ErrorResponse, "description": "Profile is deactivated / access not permitted"}}
E404 = {404: {"model": ErrorResponse, "description": "Profile not found"}}
E422 = {422: {"model": ErrorResponse, "description": "Validation error"}}


@router.post("/profile", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED,
             summary="Create the profile of the logged-in user",
             responses={409: {"model": ErrorResponse, "description": "Profile already exists"}, **E401, **E422})
def create_profile(payload: ProfileCreate, token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return user_service.create_profile(db, token.user_id, payload)


@router.get("/profile", response_model=ProfileResponse, summary="Get the logged-in user's profile",
            responses={**E401, **E404})
def get_profile(profile: UserProfile = Depends(user_service.require_profile)):
    return profile


@router.put("/profile", response_model=ProfileResponse, summary="Replace the profile (full update)",
            responses={**E401, **E403, **E404, **E422})
def update_profile(payload: ProfileUpdate, profile: UserProfile = Depends(user_service.require_active_profile),
                   db: Session = Depends(get_db)):
    return user_service.replace_profile(db, profile, payload)


@router.patch("/profile", response_model=ProfileResponse, summary="Partially update the profile",
              responses={**E401, **E403, **E404, **E422})
def patch_profile(payload: ProfilePatch, profile: UserProfile = Depends(user_service.require_active_profile),
                  db: Session = Depends(get_db)):
    return user_service.patch_profile(db, profile, payload)


@router.delete("/profile", response_model=MessageResponse,
               summary="Delete the profile and all its addresses (the login account is not touched)",
               responses={**E401, **E404})
def delete_profile(profile: UserProfile = Depends(user_service.require_profile), db: Session = Depends(get_db)):
    user_service.delete_profile(db, profile)
    return MessageResponse(message="Profile and addresses deleted successfully")


@router.post("/profile/deactivate", response_model=ProfileResponse,
             summary="Deactivate the profile (kept in the database; the Authentication Service account is not changed)",
             responses={409: {"model": ErrorResponse, "description": "Already deactivated"}, **E401, **E404})
def deactivate_profile(profile: UserProfile = Depends(user_service.require_profile), db: Session = Depends(get_db)):
    return user_service.deactivate_profile(db, profile)


@router.post("/profile/reactivate", response_model=ProfileResponse, summary="Reactivate a deactivated profile",
             responses={409: {"model": ErrorResponse, "description": "Already active"}, **E401, **E404})
def reactivate_profile(profile: UserProfile = Depends(user_service.require_profile), db: Session = Depends(get_db)):
    return user_service.reactivate_profile(db, profile)


@router.get("/account", response_model=AccountResponse,
            summary="Account overview: token identity, profile status, address count and default address",
            responses={**E401})
def get_account(token: TokenData = Depends(get_token_data), db: Session = Depends(get_db)):
    return user_service.account_summary(db, token)
