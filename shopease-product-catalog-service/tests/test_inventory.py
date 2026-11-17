"""Inventory APIs: stock in/out, history, low stock, atomicity and concurrency. MANDATORY n = scenario n."""
import os
import tempfile
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.connection import Base, SessionLocal, get_db
from app.main import app
from tests.conftest import ADMIN, CUSTOMER, add_product, db_products, db_txns, headers

INV = "/api/inventory"


def stock(client, pid):
    return client.get(f"{INV}/{pid}", headers=ADMIN).json()["stock_quantity"]


def test_get_stock(client, product):
    r = client.get(f"{INV}/{product['id']}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["stock_quantity"] == 50 and r.json()["sku"] == "PH-001" and r.json()["is_low_stock"] is False and r.json()["low_stock_threshold"] == 10


def test_mandatory_12_stock_in(client, product):
    r = client.post(f"{INV}/{product['id']}/stock-in", json={"quantity": 25, "remarks": "Supplier delivery"}, headers=ADMIN)
    assert r.status_code == 201
    b = r.json()
    assert b["transaction_type"] == "IN" and (b["previous_stock"], b["updated_stock"], b["quantity"]) == (50, 75, 25)
    assert stock(client, product["id"]) == 75
    assert db_products()[0].stock_quantity == 75
    last = db_txns(1)[-1]
    assert (last.transaction_type, last.quantity, last.previous_stock, last.updated_stock, last.remarks) == ("IN", 25, 50, 75, "Supplier delivery")
    assert last.performed_by == 1


def test_mandatory_13_stock_out(client, product):
    r = client.post(f"{INV}/{product['id']}/stock-out", json={"quantity": 20, "remarks": "Order #1"}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["updated_stock"] == 30
    assert (r.json()["transaction_type"], r.json()["previous_stock"], r.json()["updated_stock"]) == ("OUT", 50, 30)
    assert db_products()[0].stock_quantity == 30 and db_txns(1)[-1].transaction_type == "OUT"
    assert client.post(f"{INV}/{product['id']}/stock-out", json={"quantity": 30}, headers=ADMIN).json()["updated_stock"] == 0  # exactly to zero


def test_mandatory_14_stock_cannot_go_below_zero(client, product):
    r = client.post(f"{INV}/{product['id']}/stock-out", json={"quantity": 51, "remarks": "too many"}, headers=ADMIN)
    assert r.status_code == 409 and r.json()["message"] == "Insufficient stock: 50 available, 51 requested"
    assert db_products()[0].stock_quantity == 50 and len(db_txns(1)) == 1  # nothing changed, nothing recorded


@pytest.mark.parametrize("qty", [0, -5, 1.5, "abc", None, 1_000_000_001])
def test_quantity_must_be_a_positive_integer(client, product, qty):
    for action in ("stock-in", "stock-out"):
        r = client.post(f"{INV}/{product['id']}/{action}", json={"quantity": qty}, headers=ADMIN)
        assert r.status_code == 422 and r.json()["details"][0]["field"] == "quantity"
    assert db_products()[0].stock_quantity == 50 and len(db_txns(1)) == 1


def test_stock_in_cannot_exceed_maximum(client, product):
    r = client.post(f"{INV}/{product['id']}/stock-in", json={"quantity": 1_000_000_000}, headers=ADMIN)
    assert r.status_code == 409 and "exceed" in r.json()["message"]
    assert db_products()[0].stock_quantity == 50


def test_remarks_validation_and_unknown_fields(client, product):
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1, "remarks": "x" * 256}, headers=ADMIN).status_code == 422
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1, "stock_quantity": 5}, headers=ADMIN).status_code == 422
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1}, headers=ADMIN).status_code == 201  # remarks optional


def test_unknown_product(client, product):
    for call in (lambda: client.get(f"{INV}/999", headers=ADMIN), lambda: client.post(f"{INV}/999/stock-in", json={"quantity": 1}, headers=ADMIN),
                 lambda: client.post(f"{INV}/999/stock-out", json={"quantity": 1}, headers=ADMIN), lambda: client.get(f"{INV}/999/history", headers=ADMIN)):
        assert call().status_code == 404
    assert client.get(f"{INV}/abc", headers=ADMIN).status_code == 422


def test_mandatory_15_history(client, product):
    pid = product["id"]
    client.post(f"{INV}/{pid}/stock-in", json={"quantity": 10, "remarks": "restock"}, headers=ADMIN)
    client.post(f"{INV}/{pid}/stock-out", json={"quantity": 4}, headers=ADMIN)
    client.post(f"{INV}/{pid}/stock-out", json={"quantity": 999}, headers=ADMIN)  # rejected: must not appear
    r = client.get(f"{INV}/{pid}/history", headers=ADMIN)
    assert r.status_code == 200 and r.json()["total"] == 3 and r.json()["product_id"] == pid
    items = r.json()["items"]  # newest first
    assert [(i["transaction_type"], i["quantity"], i["previous_stock"], i["updated_stock"]) for i in items] == \
        [("OUT", 4, 60, 56), ("IN", 10, 50, 60), ("IN", 50, 0, 50)]
    assert items[1]["remarks"] == "restock" and items[2]["remarks"] == "Initial stock" and "created_at" in items[0]
    # the chain is continuous: every row starts where the previous one ended, and the last one equals the real stock
    chain = [(t.previous_stock, t.updated_stock) for t in db_txns(pid)]
    assert all(chain[i][1] == chain[i + 1][0] for i in range(len(chain) - 1)) and chain[-1][1] == db_products()[0].stock_quantity
    assert [i["transaction_type"] for i in client.get(f"{INV}/{pid}/history?transaction_type=OUT", headers=ADMIN).json()["items"]] == ["OUT"]
    page = client.get(f"{INV}/{pid}/history?page=2&page_size=2", headers=ADMIN).json()
    assert page["total"] == 3 and len(page["items"]) == 1 and page["page"] == 2
    assert client.get(f"{INV}/{pid}/history?transaction_type=SIDEWAYS", headers=ADMIN).status_code == 422
    assert client.get(f"{INV}/{pid}/history?page_size=101", headers=ADMIN).status_code == 422


def test_mandatory_16_low_stock(client, category):
    add_product(client, sku="L-1", name="Low One", stock_quantity=3)
    add_product(client, sku="L-2", name="Edge Ten", stock_quantity=10)
    add_product(client, sku="L-3", name="Plenty", stock_quantity=11)
    add_product(client, sku="L-4", name="Empty", stock_quantity=0)
    add_product(client, sku="L-5", name="Hidden", stock_quantity=1, is_active=False)
    r = client.get(f"{INV}/low-stock", headers=ADMIN)  # default threshold = 10 from the environment
    assert r.status_code == 200 and r.json()["threshold"] == 10 and r.json()["total"] == 3
    assert [(i["sku"], i["stock_quantity"]) for i in r.json()["items"]] == [("L-4", 0), ("L-1", 3), ("L-2", 10)]  # lowest first
    assert [i["sku"] for i in client.get(f"{INV}/low-stock?threshold=3", headers=ADMIN).json()["items"]] == ["L-4", "L-1"]
    assert client.get(f"{INV}/low-stock?threshold=0", headers=ADMIN).json()["total"] == 1
    assert client.get(f"{INV}/low-stock?threshold=3&include_inactive=true", headers=ADMIN).json()["total"] == 3
    assert client.get(f"{INV}/low-stock?threshold=-1", headers=ADMIN).status_code == 422
    assert client.get(f"{INV}/low-stock?threshold=abc", headers=ADMIN).status_code == 422
    assert client.get(f"{INV}/low-stock?page_size=1", headers=ADMIN).json()["items"][0]["sku"] == "L-4"
    client.post(f"{INV}/1/stock-in", json={"quantity": 100}, headers=ADMIN)  # restocking removes it from the list
    assert "L-1" not in [i["sku"] for i in client.get(f"{INV}/low-stock", headers=ADMIN).json()["items"]]
    assert client.get(f"{INV}/low-stock", headers=ADMIN).json()["items"][0]["stock_quantity"] <= 10


def test_configurable_default_threshold(client, category, monkeypatch):
    from app.core.config import settings
    add_product(client, sku="L-1", name="Low One", stock_quantity=3)
    monkeypatch.setattr(settings, "low_stock_threshold", 2)
    assert client.get(f"{INV}/low-stock", headers=ADMIN).json()["total"] == 0
    monkeypatch.setattr(settings, "low_stock_threshold", 5)
    assert client.get(f"{INV}/low-stock", headers=ADMIN).json()["total"] == 1


def test_low_stock_route_is_not_shadowed_by_product_id(client, product):
    assert client.get(f"{INV}/low-stock", headers=ADMIN).status_code == 200


def test_inactive_product_stock_can_still_be_managed(client, product):
    client.patch(f"/api/products/{product['id']}", json={"is_active": False}, headers=ADMIN)
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 5}, headers=ADMIN).status_code == 201


# ---------------------------------------------------------------- atomicity
def test_failure_while_recording_history_rolls_back_the_stock_change(client, product, monkeypatch):
    from app.services import inventory_service
    from app.models.inventory_model import InventoryTransaction

    class Boom(RuntimeError):
        pass

    original = InventoryTransaction.__init__

    def failing_init(self, *a, **kw):
        raise Boom("history insert failed")

    monkeypatch.setattr(InventoryTransaction, "__init__", failing_init)
    safe = TestClient(app, raise_server_exceptions=False)
    r = safe.post(f"{INV}/1/stock-out", json={"quantity": 10}, headers=ADMIN)
    assert r.status_code == 500 and "history insert failed" not in r.text
    monkeypatch.setattr(InventoryTransaction, "__init__", original)
    assert db_products()[0].stock_quantity == 50 and len(db_txns(1)) == 1  # rolled back: stock unchanged, no half-written history
    assert client.post(f"{INV}/1/stock-out", json={"quantity": 10}, headers=ADMIN).json()["updated_stock"] == 40  # still usable


def test_failure_at_commit_rolls_back_both_tables(client, product, monkeypatch):
    from sqlalchemy.orm import Session
    original_commit = Session.commit
    calls = {"n": 0}

    def failing_commit(self):
        calls["n"] += 1
        raise RuntimeError("disk full")

    monkeypatch.setattr(Session, "commit", failing_commit)
    safe = TestClient(app, raise_server_exceptions=False)
    assert safe.post(f"{INV}/1/stock-in", json={"quantity": 10}, headers=ADMIN).status_code == 500
    monkeypatch.setattr(Session, "commit", original_commit)
    assert calls["n"] >= 1 and db_products()[0].stock_quantity == 50 and len(db_txns(1)) == 1


# ---------------------------------------------------------------- concurrency (file database, many threads)
@pytest.fixture
def file_db_client():
    path = os.path.join(tempfile.mkdtemp(), "concurrent.db")
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 60})
    from sqlalchemy import event

    @event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    yield TestClient(app), Session
    app.dependency_overrides.clear()
    eng.dispose()


def run_parallel(n, fn):
    barrier, results = threading.Barrier(n), [None] * n

    def worker(i):
        barrier.wait()
        results[i] = fn(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_mandatory_20_simultaneous_stock_out_never_oversells(file_db_client):
    client, Session = file_db_client
    client.post("/api/categories/", json={"name": "Concurrent"}, headers=ADMIN)
    pid = client.post("/api/products/", json={"category_id": 1, "name": "Hot Item", "sku": "HOT-1", "price": "10.00", "stock_quantity": 20}, headers=ADMIN).json()["id"]
    results = run_parallel(30, lambda i: TestClient(app).post(f"{INV}/{pid}/stock-out", json={"quantity": 1, "remarks": f"req {i}"}, headers=ADMIN).status_code)
    assert results.count(201) == 20 and results.count(409) == 10 and set(results) == {201, 409}
    from app.models.inventory_model import InventoryTransaction
    from app.models.product_model import Product
    with Session() as db:
        assert db.get(Product, pid).stock_quantity == 0
        rows = db.query(InventoryTransaction).filter_by(product_id=pid).order_by(InventoryTransaction.id).all()
    assert len(rows) == 21 and rows[0].remarks == "Initial stock"  # 1 initial + exactly 20 successful outs
    assert all(rows[i].updated_stock == rows[i + 1].previous_stock for i in range(len(rows) - 1))  # no lost update, no duplicate snapshot
    assert sorted(r.updated_stock for r in rows[1:]) == list(range(0, 20))


def test_mandatory_20_simultaneous_mixed_in_and_out_stay_consistent(file_db_client):
    client, Session = file_db_client
    client.post("/api/categories/", json={"name": "Concurrent"}, headers=ADMIN)
    pid = client.post("/api/products/", json={"category_id": 1, "name": "Mixed", "sku": "MIX-1", "price": "10.00", "stock_quantity": 100}, headers=ADMIN).json()["id"]

    def call(i):
        action = "stock-in" if i % 2 == 0 else "stock-out"
        return TestClient(app).post(f"{INV}/{pid}/{action}", json={"quantity": 3}, headers=ADMIN).status_code

    results = run_parallel(20, call)
    assert results == [201] * 20  # 100 in stock, so every request can succeed
    from app.models.product_model import Product
    from app.models.inventory_model import InventoryTransaction
    with Session() as db:
        assert db.get(Product, pid).stock_quantity == 100  # 10 x (+3) and 10 x (-3)
        rows = db.query(InventoryTransaction).filter_by(product_id=pid).order_by(InventoryTransaction.id).all()
    assert len(rows) == 21 and all(rows[i].updated_stock == rows[i + 1].previous_stock for i in range(20))


# ---------------------------------------------------------------- idempotency reference (used by the Order Service)
def test_reference_makes_stock_out_idempotent(client, product):
    body = {"quantity": 5, "remarks": "Order ORD-1", "reference": "ORD-1:reserve:1"}
    first = client.post(f"{INV}/1/stock-out", json=body, headers=ADMIN)
    again = client.post(f"{INV}/1/stock-out", json=body, headers=ADMIN)
    assert first.status_code == 201 and again.status_code == 200  # the replay is recognised
    assert first.json()["id"] == again.json()["id"] and again.json()["reference"] == "ORD-1:reserve:1"
    assert stock(client, 1) == 45 and len(db_txns(1)) == 2  # deducted once only


def test_reference_reused_for_a_different_operation_is_refused(client, product):
    client.post(f"{INV}/1/stock-out", json={"quantity": 5, "reference": "R-1"}, headers=ADMIN)
    for call in (lambda: client.post(f"{INV}/1/stock-out", json={"quantity": 6, "reference": "R-1"}, headers=ADMIN),
                 lambda: client.post(f"{INV}/1/stock-in", json={"quantity": 5, "reference": "R-1"}, headers=ADMIN)):
        assert call().status_code == 409
    assert stock(client, 1) == 45


def test_reference_validation_and_optional(client, product):
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1, "reference": "bad ref!"}, headers=ADMIN).status_code == 422
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1, "reference": "x" * 101}, headers=ADMIN).status_code == 422
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1, "reference": "  "}, headers=ADMIN).status_code == 201  # blank = none
    assert client.post(f"{INV}/1/stock-in", json={"quantity": 1}, headers=ADMIN).status_code == 201  # no reference: always a new change
    assert client.post(f"{INV}/999/stock-in", json={"quantity": 1, "reference": "R-9"}, headers=ADMIN).status_code == 404


def test_simultaneous_requests_with_the_same_reference_deduct_once(file_db_client):
    client, Session = file_db_client
    client.post("/api/categories/", json={"name": "Idem"}, headers=ADMIN)
    pid = client.post("/api/products/", json={"category_id": 1, "name": "Idem Item", "sku": "ID-1", "price": "10.00", "stock_quantity": 10}, headers=ADMIN).json()["id"]
    results = run_parallel(12, lambda i: TestClient(app).post(f"{INV}/{pid}/stock-out", json={"quantity": 4, "reference": "SAME-OP"}, headers=ADMIN).status_code)
    assert set(results) <= {200, 201} and results.count(201) == 1
    from app.models.product_model import Product
    with Session() as db:
        assert db.get(Product, pid).stock_quantity == 6  # 10 - 4, once
