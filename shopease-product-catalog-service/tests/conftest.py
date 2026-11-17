"""Test setup: in-memory SQLite and tokens signed like the Authentication Service signs them.
The environment variables must be set BEFORE the application is imported."""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-automated-tests-only-0123456789"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["AUTH_VERIFY_REMOTE"] = "false"
os.environ["ADMIN_ROLE"] = "admin"
os.environ["LOW_STOCK_THRESHOLD"] = "10"

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.database.connection import Base, SessionLocal, engine
from app.main import app
from app.models.category_model import Category
from app.models.inventory_model import InventoryTransaction
from app.models.product_model import Product

SECRET = os.environ["JWT_SECRET_KEY"]


def make_token(user_id: int = 1, role: str | None = "admin", expires_in: int = 1800) -> str:
    """Same claims as the Authentication Service: sub (user id), role, iat, exp."""
    now = int(time.time())
    claims = {"sub": str(user_id), "iat": now, "exp": now + expires_in}
    if role is not None:
        claims["role"] = role
    return jwt.encode(claims, SECRET, algorithm="HS256")


def headers(user_id: int = 1, role: str | None = "admin", **kw) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role, **kw)}"}


ADMIN = headers(1, "admin")
CUSTOMER = headers(2, "customer")

CATEGORY = {"name": "Electronics", "description": "Phones and gadgets"}


def product_body(**overrides) -> dict:
    body = {"category_id": 1, "name": "Smart Phone X", "description": "6.5 inch display", "sku": "PH-001",
            "price": "20000.00", "discount_price": "18000.00", "stock_quantity": 50,
            "image_url": "https://example.com/phone.png", "brand": "Acme", "is_active": True}
    body.update(overrides)
    return body


def put_body(**overrides) -> dict:
    """Full replacement body for PUT: stock is managed only through the inventory APIs."""
    body = product_body(**overrides)
    body.pop("stock_quantity", None)
    return body


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def category(client):
    r = client.post("/api/categories/", json=CATEGORY, headers=ADMIN)
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def product(client, category):
    r = client.post("/api/products/", json=product_body(), headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def add_product(client, **overrides) -> dict:
    r = client.post("/api/products/", json=product_body(**overrides), headers=ADMIN)
    assert r.status_code == 201, r.text
    return r.json()


def db_products():
    with SessionLocal() as db:
        return db.query(Product).order_by(Product.id).all()


def db_categories():
    with SessionLocal() as db:
        return db.query(Category).order_by(Category.id).all()


def db_txns(product_id: int | None = None):
    with SessionLocal() as db:
        q = db.query(InventoryTransaction)
        if product_id is not None:
            q = q.filter_by(product_id=product_id)
        return q.order_by(InventoryTransaction.id).all()
