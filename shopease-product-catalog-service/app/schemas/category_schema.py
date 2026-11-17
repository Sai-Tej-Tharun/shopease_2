"""Pydantic schemas (validation rules) for the category APIs."""
import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

_NAME_RE = re.compile(r"^[\w &,.'/()+-]+$")


def clean_name(value: str, label: str, max_len: int) -> str:
    value = " ".join(value.split())
    if not (2 <= len(value) <= max_len):
        raise ValueError(f"{label} must be between 2 and {max_len} characters")
    if not _NAME_RE.match(value) or not any(ch.isalnum() for ch in value):
        raise ValueError(f"{label} may contain only letters, digits, spaces and & , . ' / ( ) + -")
    return value


def clean_description(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > 2000:
        raise ValueError("Description must be at most 2000 characters")
    return value or None


class CategoryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return clean_name(v, "Category name", 100)

    @field_validator("description")
    @classmethod
    def _description(cls, v: str | None) -> str | None:
        return clean_description(v)


class CategoryCreate(CategoryBase):
    """POST body."""


class CategoryUpdate(CategoryBase):
    """PUT body: replaces name, description and status."""


class CategoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
