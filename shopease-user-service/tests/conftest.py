"""Test setup: in-memory SQLite and tokens signed like the Authentication Service signs them.
The environment variables must be set BEFORE the application is imported."""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-automated-tests-only-0123456789"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_VERIFY_REMOTE"] = "false"

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.database.connection import Base, SessionLocal, engine
from app.main import app
from app.models.address_model import UserAddress
from app.models.user_model import UserProfile

SECRET = os.environ["JWT_SECRET_KEY"]


def make_token(user_id: int = 1, role: str = "customer", expires_in: int = 1800) -> str:
    """Same claims as the Authentication Service: sub (user id), role, iat, exp."""
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "role": role, "iat": now, "exp": now + expires_in}, SECRET, algorithm="HS256")


def headers(user_id: int = 1, **kw) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, **kw)}"}


PROFILE = {"full_name": "Sai Tej", "phone_number": "9876543210", "date_of_birth": "2000-05-17",
           "gender": "Male", "profile_image": "https://example.com/sai.png"}

HOME = {"address_type": "Home", "full_name": "Sai Tej", "phone_number": "9876543210",
        "address_line_1": "12-3 Main Road", "address_line_2": "Near Park", "city": "Hyderabad",
        "state": "Telangana", "country": "India", "postal_code": "500001", "is_default": True}
OFFICE = {**HOME, "address_type": "Office", "address_line_1": "5 Tech Park", "address_line_2": None,
          "postal_code": "500081", "is_default": False}
OTHER = {**HOME, "address_type": "Other", "full_name": "Ravi Kumar", "address_line_1": "77 Lake View",
         "city": "Chennai", "state": "Tamil Nadu", "postal_code": "600001", "is_default": False}


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def profile(client):
    """User 1 with a profile."""
    r = client.post("/api/users/profile", json=PROFILE, headers=headers(1))
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def profile2(client):
    r = client.post("/api/users/profile", json={**PROFILE, "full_name": "Other Person", "phone_number": "9123456780"}, headers=headers(2))
    assert r.status_code == 201
    return r.json()


def db_profiles():
    with SessionLocal() as db:
        return db.query(UserProfile).order_by(UserProfile.id).all()


def db_addresses(user_id: int | None = None):
    with SessionLocal() as db:
        q = db.query(UserAddress)
        if user_id is not None:
            q = q.filter_by(user_id=user_id)
        return q.order_by(UserAddress.id).all()
