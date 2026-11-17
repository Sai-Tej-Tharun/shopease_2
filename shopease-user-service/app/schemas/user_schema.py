"""Pydantic schemas (validation rules) for the profile APIs."""
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.schemas.address_schema import AddressResponse, clean_person_name, clean_phone

GENDERS = ("Male", "Female", "Other", "Prefer not to say")


class ProfileFields(BaseModel):
    """Field rules shared by create / update / partial update. Unknown fields (for example auth_user_id)
    are rejected: the owner of a profile is always taken from the JWT."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("full_name", check_fields=False)
    @classmethod
    def _name(cls, v):
        return None if v is None else clean_person_name(v, "Full name")

    @field_validator("phone_number", check_fields=False)
    @classmethod
    def _phone(cls, v):
        return None if v is None else clean_phone(v)

    @field_validator("date_of_birth", check_fields=False)
    @classmethod
    def _dob(cls, v):
        if v is None:
            return None
        if v >= date.today():
            raise ValueError("Date of birth must be in the past")
        if v < date(1900, 1, 1):
            raise ValueError("Date of birth must not be before 1900-01-01")
        return v

    @field_validator("gender", check_fields=False)
    @classmethod
    def _gender(cls, v):
        if v is None:
            return None
        for allowed in GENDERS:
            if v.strip().lower() == allowed.lower():
                return allowed
        raise ValueError("Gender must be Male, Female, Other or Prefer not to say")

    @field_validator("profile_image", check_fields=False)
    @classmethod
    def _image(cls, v):
        if v is None:
            return None
        v = v.strip()
        if v == "":
            return None
        if len(v) > 255:
            raise ValueError("Profile image must be at most 255 characters")
        if any(ch.isspace() for ch in v) or not v.startswith(("http://", "https://", "/")):
            raise ValueError("Profile image must be an http(s) URL or a path starting with /")
        return v


class ProfileCreate(ProfileFields):
    full_name: str
    phone_number: str
    date_of_birth: date | None = None
    gender: str | None = None
    profile_image: str | None = None


class ProfileUpdate(ProfileCreate):
    """PUT body: replaces the profile; optional fields that are left out are cleared."""


class ProfilePatch(ProfileFields):
    """PATCH body: only the fields that are sent are changed."""

    full_name: str | None = None
    phone_number: str | None = None
    date_of_birth: date | None = None
    gender: str | None = None
    profile_image: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self):
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        for required in ("full_name", "phone_number"):
            if required in self.model_fields_set and getattr(self, required) is None:
                raise ValueError(f"{required} cannot be null")
        return self


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    auth_user_id: int
    full_name: str
    phone_number: str
    date_of_birth: date | None
    gender: str | None
    profile_image: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AccountResponse(BaseModel):
    """Account overview built from the token and the local profile."""

    auth_user_id: int
    role: str
    has_profile: bool
    profile_status: str  # "none" | "active" | "deactivated"
    profile: ProfileResponse | None
    address_count: int
    default_address: AddressResponse | None


class MessageResponse(BaseModel):
    message: str


class ErrorResponse(BaseModel):
    status_code: int
    error: str
    message: str
    details: list[dict] | None = None
