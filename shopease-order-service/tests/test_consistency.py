"""Failure handling and consistency between the Order Service and the Product Catalog Service.
MANDATORY 19 = simulate Product Catalog unavailability, 20 = verify order and inventory consistency after a failed operation."""
import httpx
import pytest
from sqlalchemy.orm import Session

from tests.conftest import ADMIN, ALICE, body, db_compensations, db_items, db_orders, place

URL = "/api/orders/"


def total_reserved(catalog):
    return sum(1 for r in catalog.ops if ":reserve:" in r), sum(1 for r in catalog.ops if ":release:" in r)


def test_mandatory_19_catalog_down_when_placing_an_order(client, catalog):
    catalog.fail(lambda r: True, "down")
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 503 and r.json()["message"] == "The Product Catalog Service is unavailable. Please try again later."
    assert r.json()["error"] == "Service Unavailable"
    assert db_orders() == [] and db_items() == [] and db_compensations() == []  # no half-created order, nothing to compensate


def test_mandatory_20_consistency_after_the_failed_operation_and_recovery(client, catalog):
    stock_before = (catalog.stock(1), catalog.stock(2))
    catalog.fail(lambda r: True, "timeout", times=6)  # unavailable, then back
    assert client.post(URL, json=body(), headers=ALICE).status_code == 503
    assert (catalog.stock(1), catalog.stock(2)) == stock_before and catalog.ops == {} and db_orders() == []
    catalog.clear_faults()
    o = place(client)  # the very next order works normally
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99) and len(db_orders()) == 1 and o["order_status"] == "PENDING"


def test_user_service_unavailable(client, catalog, users):
    users.fail(lambda r: True, "down")
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 503 and r.json()["message"] == "The User Management Service is unavailable. Please try again later."
    assert db_orders() == [] and catalog.calls == []  # we never touched the catalog
    users.clear_faults()
    users.fail(lambda r: True, "500", times=1)  # one 5xx answer is retried
    assert client.post(URL, json=body(), headers=ALICE).status_code == 201


def test_user_service_rejecting_the_token_is_reported(client, catalog, users, monkeypatch):
    from app.services import user_client
    monkeypatch.setattr(user_client, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(401, json={"message": "Invalid token"})))
    assert client.post(URL, json=body(), headers=ALICE).status_code == 401


def test_transient_catalog_errors_are_retried(client, catalog):
    catalog.fail(lambda r: r.method == "GET", "500", times=1)
    catalog.fail(lambda r: "stock-out" in r.url.path, "timeout", times=1)
    assert client.post(URL, json=body(), headers=ALICE).status_code == 201
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99)


def test_lost_answer_on_reservation_does_not_deduct_twice(client, catalog):
    catalog.fail(lambda r: "stock-out" in r.url.path and "/1/" in r.url.path, "timeout_after", times=1)  # applied, answer lost
    o = place(client)  # the retry carries the same reference and is answered "already applied"
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99)  # 2 deducted once, not 4
    assert o["items"][0]["quantity"] == 2


def test_second_line_refused_releases_the_first_line(client, catalog):
    # the pre-check passes (stock looks fine), but somebody buys product 2 before our reservation arrives
    original = catalog._stock

    def racing(request, pid, kind, mode):
        if pid == 2 and kind == "OUT":
            catalog.products[2]["stock_quantity"] = 0
        return original(request, pid, kind, mode)

    catalog._stock = racing
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 409 and r.json()["message"].startswith("Insufficient stock for USB Cable")
    assert db_orders() == [] and db_compensations() == []
    assert catalog.stock(1) == 10  # product 1 was reserved (-2) and then released (+2)
    assert total_reserved(catalog) == (1, 1)


def test_catalog_goes_down_after_the_first_reservation(client, catalog):
    catalog.fail(lambda r: "stock-out" in r.url.path and "/2/" in r.url.path, "down")  # second line cannot be reserved
    catalog.fail(lambda r: "stock-in" in r.url.path, "down", times=2)  # ... and the first release attempt fails too
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 503 and db_orders() == []
    # product 1 is still reserved; the release plus the unknown outcome of product 2 are stored for retry
    pending = db_compensations()
    assert [(c.product_id, c.reservation_confirmed, c.status) for c in pending] == [(1, True, "PENDING"), (2, False, "PENDING")]
    assert catalog.stock(1) == 8
    catalog.clear_faults()
    r = client.post(f"{URL}compensations/retry", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"processed": 2, "completed": 2, "still_pending": 0}
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # everything is back, and product 2 was never deducted
    assert all(c.status == "DONE" for c in db_compensations())
    again = client.post(f"{URL}compensations/retry", headers=ADMIN).json()
    assert again == {"processed": 0, "completed": 0, "still_pending": 0} and (catalog.stock(1), catalog.stock(2)) == (10, 100)


def test_unknown_reservation_outcome_that_was_applied_is_undone(client, catalog):
    # product 1: the reservation IS applied but the answer is lost, and the retry also times out -> the outcome is unknown
    catalog.fail(lambda r: "stock-out" in r.url.path and "/1/" in r.url.path, "timeout_after", times=1)
    catalog.fail(lambda r: "stock-out" in r.url.path and "/1/" in r.url.path, "timeout", times=1)
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 503 and db_orders() == []
    assert catalog.ops[next(iter(catalog.ops))][0] == 1  # the catalog really did deduct product 1 ...
    # ... and the compensation found out by repeating the reservation (same reference answers "already applied") and released it
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100) and db_compensations() == []
    assert total_reserved(catalog) == (1, 1)


def test_unknown_reservation_outcome_that_was_not_applied_changes_nothing(client, catalog):
    catalog.fail(lambda r: "stock-out" in r.url.path and "/1/" in r.url.path, "timeout", times=2)  # nothing applied, nothing answered
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 503 and db_orders() == []
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # the verification reservation + release cancel each other out
    assert total_reserved(catalog) == (1, 1) and db_compensations() == []


def test_failure_saving_the_order_releases_the_stock(client, catalog, monkeypatch):
    original = Session.commit
    state = {"n": 0}

    def failing_commit(self):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("disk full")
        return original(self)

    monkeypatch.setattr(Session, "commit", failing_commit)
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app, raise_server_exceptions=False).post(URL, json=body(), headers=ALICE)
    assert r.status_code == 500 and "disk full" not in r.text
    monkeypatch.setattr(Session, "commit", original)
    assert db_orders() == [] and db_items() == []  # no incomplete local record
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # the reservation was compensated
    assert total_reserved(catalog) == (2, 2)


def test_compensation_list_is_admin_only(client, catalog):
    assert client.get(f"{URL}compensations", headers=ALICE).status_code == 403
    assert client.post(f"{URL}compensations/retry", headers=ALICE).status_code == 403
    r = client.get(f"{URL}compensations", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"items": [], "pending": 0}


def test_catalog_rejecting_service_token_is_reported_as_bad_gateway(client, catalog, monkeypatch):
    from app.services import product_client
    monkeypatch.setattr(product_client, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(403, json={"message": "no"})))
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 502 and "rejected the Order Service's credentials" in r.json()["message"] and db_orders() == []


def test_concurrent_orders_for_the_last_units_never_oversell(file_db_client, catalog):
    import threading
    from fastapi.testclient import TestClient
    from app.main import app
    catalog.add(9, "Last Units", "10.00", 5)
    results = [None] * 10
    barrier = threading.Barrier(10)

    def worker(i):
        barrier.wait()
        results[i] = TestClient(app).post(URL, json=body(items=[{"product_id": 9, "quantity": 1}]), headers=ALICE).status_code

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(201) == 5 and results.count(409) == 5  # exactly the available units were sold
    assert catalog.stock(9) == 0
    assert file_db_client.get(URL, headers=ADMIN).json()["total"] == 5  # five saved orders (read through the API: this test uses a file database)
    assert file_db_client.get(f"{URL}compensations", headers=ADMIN).json()["pending"] == 0
