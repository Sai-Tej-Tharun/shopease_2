"""Test setup: in-memory SQLite, tokens signed like the Authentication Service signs them, and in-process FAKE
Product Catalog and User services behind httpx.MockTransport (stateful, with fault injection).
Environment variables must be set BEFORE the application is imported."""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-automated-tests-only-0123456789"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["ADMIN_ROLE"] = "admin"
os.environ["AUTH_VERIFY_REMOTE"] = "false"
os.environ["USER_SERVICE_URL"] = "http://user.test"
os.environ["PRODUCT_SERVICE_URL"] = "http://catalog.test"
os.environ["SERVICE_RETRY_ATTEMPTS"] = "2"
os.environ["TAX_RATE"] = "0.18"
os.environ["SHIPPING_FLAT_RATE"] = "50.00"
os.environ["FREE_SHIPPING_THRESHOLD"] = "1000.00"

import json
import threading
import time
from decimal import Decimal

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.database.connection import Base, SessionLocal, engine
from app.main import app
from app.models.order_item_model import OrderItem
from app.models.order_model import Order
from app.models.stock_compensation_model import StockCompensation
from app.services import product_client, user_client

SECRET = os.environ["JWT_SECRET_KEY"]


def make_token(user_id: int = 1, role: str | None = "customer", expires_in: int = 1800) -> str:
    now = int(time.time())
    claims = {"sub": str(user_id), "iat": now, "exp": now + expires_in}
    if role is not None:
        claims["role"] = role
    return jwt.encode(claims, SECRET, algorithm="HS256")


def headers(user_id: int = 1, role: str | None = "customer", **kw) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id, role, **kw)}"}


ALICE = headers(11, "customer")   # profile id 101, addresses 1 and 2
BOB = headers(12, "customer")     # profile id 102, address 3
ADMIN = headers(1, "admin")


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


def _error(status: int, message: str) -> httpx.Response:
    return _json(status, {"status_code": status, "error": "Error", "message": message})


def _caller(request: httpx.Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        return jwt.decode(auth[7:], SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


class Fake:
    """Common fault injection: faults = list of {"match": callable(request), "mode": ..., "times": int|None}.
    modes: down (connection refused), timeout (nothing applied), timeout_after (applied, then the answer is lost), 500."""

    def __init__(self):
        self.lock = threading.Lock()
        self.faults: list[dict] = []
        self.calls: list[tuple[str, str]] = []

    def fail(self, match, mode="down", times=None):
        self.faults.append({"match": match, "mode": mode, "times": times})

    def clear_faults(self):
        self.faults.clear()

    def _fault(self, request):
        for f in self.faults:
            if f["match"](request) and (f["times"] is None or f["times"] > 0):
                if f["times"] is not None:
                    f["times"] -= 1
                return f["mode"]
        return None

    def count(self, method: str, fragment: str) -> int:
        return sum(1 for m, p in self.calls if m == method and fragment in p)


class FakeCatalog(Fake):
    """Behaves like the real Product Catalog Service for the calls the Order Service makes."""

    def __init__(self):
        super().__init__()
        self.products: dict[int, dict] = {}
        self.ops: dict[str, tuple] = {}

    def add(self, pid, name, price, stock, discount=None, active=True, sku=None):
        self.products[pid] = {"id": pid, "name": name, "sku": sku or f"SKU-{pid}", "price": price, "discount_price": discount,
                              "stock_quantity": stock, "is_active": active}

    def stock(self, pid):
        return self.products[pid]["stock_quantity"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        mode = self._fault(request)
        if mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if mode == "500":
            return _error(500, "boom")
        claims = _caller(request)
        if claims is None:
            return _error(401, "Invalid token")
        if str(claims.get("role", "")).lower() != "admin":
            return _error(403, "Administrator access is required for this operation")
        parts = path.strip("/").split("/")
        if request.method == "GET" and parts[:2] == ["api", "products"] and len(parts) == 3:
            p = self.products.get(int(parts[2]))
            if p is None:
                return _error(404, f"Product with id {parts[2]} not found")
            return _json(200, {**p, "price": f"{Decimal(str(p['price'])):.2f}",
                               "discount_price": None if p["discount_price"] is None else f"{Decimal(str(p['discount_price'])):.2f}"})
        if request.method == "POST" and parts[:2] == ["api", "inventory"] and len(parts) == 4 and parts[3] in ("stock-in", "stock-out"):
            return self._stock(request, int(parts[2]), "OUT" if parts[3] == "stock-out" else "IN", mode)
        return _error(404, "Not Found")

    def _stock(self, request, pid, kind, mode):
        body = json.loads(request.content)
        qty, ref = body["quantity"], body.get("reference")
        with self.lock:
            p = self.products.get(pid)
            if p is None:
                return _error(404, f"Product with id {pid} not found")
            if ref and ref in self.ops:
                if self.ops[ref][:3] == (pid, kind, qty):
                    return _json(200, {"id": 1, "reference": ref})
                return _error(409, f"Reference '{ref}' was already used for a different stock operation")
            if kind == "OUT" and p["stock_quantity"] < qty:
                return _error(409, f"Insufficient stock: {p['stock_quantity']} available, {qty} requested")
            p["stock_quantity"] += qty if kind == "IN" else -qty
            if ref:
                self.ops[ref] = (pid, kind, qty)
        if mode == "timeout_after":  # the change was applied, but the answer never arrives
            raise httpx.ReadTimeout("timed out", request=request)
        return _json(201, {"id": 1, "reference": ref, "updated_stock": p["stock_quantity"]})


class FakeUsers(Fake):
    def __init__(self):
        super().__init__()
        self.profiles = {11: {"id": 101, "auth_user_id": 11, "full_name": "Alice Kumar", "phone_number": "9876543210", "is_active": True},
                         12: {"id": 102, "auth_user_id": 12, "full_name": "Bob Rao", "phone_number": "9123456780", "is_active": True},
                         1: {"id": 100, "auth_user_id": 1, "full_name": "Admin", "phone_number": "9000000000", "is_active": True}}
        base = {"address_type": "home", "address_line_2": None, "state": "Telangana", "country": "India", "is_default": False, "created_at": "2026-11-16T00:00:00"}
        self.addresses = {1: {**base, "id": 1, "user_id": 101, "full_name": "Alice Kumar", "phone_number": "9876543210", "address_line_1": "12 Rose Street",
                              "city": "Hyderabad", "postal_code": "500001", "is_default": True},
                          2: {**base, "id": 2, "user_id": 101, "address_type": "work", "full_name": "Alice Kumar", "phone_number": "9876543210",
                              "address_line_1": "5 Tech Park", "city": "Hyderabad", "postal_code": "500081"},
                          3: {**base, "id": 3, "user_id": 102, "full_name": "Bob Rao", "phone_number": "9123456780", "address_line_1": "9 Lake View",
                              "city": "Warangal", "postal_code": "506002"}}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        mode = self._fault(request)
        if mode == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if mode == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        if mode == "500":
            return _error(500, "boom")
        claims = _caller(request)
        if claims is None:
            return _error(401, "Invalid token")
        profile = self.profiles.get(int(claims["sub"]))
        parts = request.url.path.strip("/").split("/")
        if parts == ["api", "users", "profile"]:
            return _error(404, "Profile not found. Create your profile first") if profile is None else _json(200, profile)
        if parts[:3] == ["api", "users", "addresses"] and len(parts) == 4:
            if profile is None:
                return _error(404, "Profile not found")
            if not profile["is_active"]:
                return _error(403, "Your profile is deactivated")
            a = self.addresses.get(int(parts[3]))
            if a is None:
                return _error(404, "Address not found")
            if a["user_id"] != profile["id"]:
                return _error(403, "You do not have permission to access this address")  # what the real User Service answers
            return _json(200, a)
        return _error(404, "Not Found")


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)


@pytest.fixture
def catalog(monkeypatch):
    fake = FakeCatalog()
    fake.add(1, "Smart Phone X", "20000.00", 10, discount="18000.00")
    fake.add(2, "USB Cable", "299.00", 100, discount="199.00")
    fake.add(3, "Old Phone", "3000.00", 5, active=False)
    fake.add(4, "Laptop Pro", "60000.00", 3)
    monkeypatch.setattr(product_client, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def users(monkeypatch):
    fake = FakeUsers()
    monkeypatch.setattr(user_client, "TRANSPORT", httpx.MockTransport(fake))
    return fake


@pytest.fixture
def client(catalog, users):
    return TestClient(app)


def body(items=None, address=1, **extra):
    data = {"items": items if items is not None else [{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 1}],
            "shipping_address_id": address}
    data.update(extra)
    return data


def place(client, who=ALICE, **kw):
    r = client.post("/api/orders/", json=body(**kw), headers=who)
    assert r.status_code == 201, r.text
    return r.json()


def db_orders():
    with SessionLocal() as db:
        return db.query(Order).order_by(Order.id).all()


def db_items(order_id=None):
    with SessionLocal() as db:
        q = db.query(OrderItem)
        if order_id:
            q = q.filter_by(order_id=order_id)
        return q.order_by(OrderItem.id).all()


def db_compensations():
    with SessionLocal() as db:
        return db.query(StockCompensation).order_by(StockCompensation.id).all()


@pytest.fixture
def file_db_client(catalog, users):
    """The application on a file-based SQLite database, for tests that use several threads."""
    import tempfile
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.database.connection import get_db
    path = os.path.join(tempfile.mkdtemp(), "orders.db")
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 60})

    @event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    maker = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def override():
        db = maker()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()
    eng.dispose()
