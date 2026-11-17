"""Pydantic schemas (validation rules) for the address APIs."""
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

ADDRESS_TYPES = ("Home", "Office", "Other")
_PHONE_RE = re.compile(r"^\+?[0-9]{10,15}$")
_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{1,8}[A-Za-z0-9]$")


def clean_phone(value: str) -> str:
    """Accepts 9876543210, +91 98765-43210, (987) 654 3210 ... and stores digits with an optional +."""
    cleaned = re.sub(r"[\s\-().]", "", value.strip())
    if not _PHONE_RE.match(cleaned):
        raise ValueError("Phone number must contain 10 to 15 digits, optionally starting with +")
    return cleaned


def clean_person_name(value: str, label: str = "Name") -> str:
    value = " ".join(value.split())
    if not (2 <= len(value) <= 100):
        raise ValueError(f"{label} must be between 2 and 100 characters")
    if not any(ch.isalpha() for ch in value) or any(not (ch.isalpha() or ch in " .'-") for ch in value):
        raise ValueError(f"{label} may contain only letters, spaces and . ' -")
    return value


def clean_place(value: str, label: str) -> str:
    value = " ".join(value.split())
    if not (2 <= len(value) <= 100):
        raise ValueError(f"{label} must be between 2 and 100 characters")
    if not any(ch.isalpha() for ch in value) or any(not (ch.isalpha() or ch in " .'-") for ch in value):
        raise ValueError(f"{label} may contain only letters, spaces and . ' -")
    return value


class AddressBase(BaseModel):
    model_config = ConfigDict(extra="forbid")  # unknown fields (e.g. user_id) are rejected, never trusted

    address_type: str
    full_name: str
    phone_number: str
    address_line_1: str
    address_line_2: str | None = None
    city: str
    state: str
    country: str
    postal_code: str
    is_default: bool = False

    @field_validator("address_type")
    @classmethod
    def _type(cls, v: str) -> str:
        for allowed in ADDRESS_TYPES:
            if v.strip().lower() == allowed.lower():
                return allowed
        raise ValueError("Address type must be Home, Office or Other")

    @field_validator("full_name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_person_name(v, "Recipient name")

    @field_validator("phone_number")
    @classmethod
    def _phone(cls, v: str) -> str:
        return clean_phone(v)

    @field_validator("address_line_1")
    @classmethod
    def _line1(cls, v: str) -> str:
        v = " ".join(v.split())
        if not (3 <= len(v) <= 255):
            raise ValueError("Address line 1 must be between 3 and 255 characters")
        return v

    @field_validator("address_line_2")
    @classmethod
    def _line2(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = " ".join(v.split())
        if len(v) > 255:
            raise ValueError("Address line 2 must be at most 255 characters")
        return v or None

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        return clean_place(v, "City")

    @field_validator("state")
    @classmethod
    def _state(cls, v: str) -> str:
        return clean_place(v, "State")

    @field_validator("country")
    @classmethod
    def _country(cls, v: str) -> str:
        return clean_place(v, "Country")

    @field_validator("postal_code")
    @classmethod
    def _postal(cls, v: str) -> str:
        v = v.strip().upper()
        if not _POSTAL_RE.match(v):
            raise ValueError("Postal code must be 3 to 10 characters (letters, digits, space or hyphen)")
        return v


class AddressCreate(AddressBase):
    """POST body. The owner is never part of the body: it comes from the JWT."""


class AddressUpdate(AddressBase):
    """PUT body: replaces all editable fields of the address."""


class AddressResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    address_type: str
    full_name: str
    phone_number: str
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    country: str
    postal_code: str
    is_default: bool
    created_at: datetime
