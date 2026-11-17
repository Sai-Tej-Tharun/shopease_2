"""Order status workflow, cancellation and stock restoration. MANDATORY n = scenario n of the task."""
import threading

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import ADMIN, ALICE, BOB, db_items, db_orders, place

URL = "/api/orders/"
ALLOWED = {("PENDING", "CONFIRMED"), ("CONFIRMED", "PROCESSING"), ("PROCESSING", "SHIPPED"), ("SHIPPED", "DELIVERED")}
ORDER = ["PENDING", "CONFIRMED", "PROCESSING", "SHIPPED", "DELIVERED"]


def advance(client, order_id, to):
    for status in ORDER[1:ORDER.index(to) + 1]:
        assert client.patch(f"{URL}{order_id}/status", json={"status": status}, headers=ADMIN).status_code == 200


def test_mandatory_12_admin_updates_status(client, catalog):
    o = place(client)
    r = client.patch(f"{URL}{o['id']}/status", json={"status": "CONFIRMED"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["order_status"] == "CONFIRMED" and r.json()["items"]
    assert r.json()["updated_at"] >= o["updated_at"]
    assert db_orders()[0].order_status == "CONFIRMED"
    for status in ("PROCESSING", "SHIPPED", "DELIVERED"):  # continue along the workflow
        assert client.patch(f"{URL}{o['id']}/status", json={"status": status}, headers=ADMIN).json()["order_status"] == status
    assert client.get(f"{URL}{o['id']}", headers=ALICE).json()["order_status"] == "DELIVERED"


def test_mandatory_13_customer_cannot_change_status(client, catalog):
    o = place(client)
    for who in (ALICE, BOB):
        r = client.patch(f"{URL}{o['id']}/status", json={"status": "CONFIRMED"}, headers=who)
        assert r.status_code == 403 and r.json()["message"] == "Administrator access is required for this operation"
    assert client.patch(f"{URL}{o['id']}/status", json={"status": "CONFIRMED"}).status_code == 401
    assert db_orders()[0].order_status == "PENDING"


@pytest.mark.parametrize("current", ORDER + ["CANCELLED"])
@pytest.mark.parametrize("target", ORDER + ["CANCELLED"])
def test_mandatory_14_status_transition_matrix(client, catalog, current, target):
    o = place(client)
    if current == "CANCELLED":
        client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    else:
        advance(client, o["id"], current)
    r = client.patch(f"{URL}{o['id']}/status", json={"status": target}, headers=ADMIN)
    ok = (current, target) in ALLOWED or (target == "CANCELLED" and current in ("PENDING", "CONFIRMED", "PROCESSING", "CANCELLED"))  # CANCELLED -> CANCELLED is an idempotent no-op
    if ok:
        assert r.status_code == 200 and r.json()["order_status"] == target
    else:
        assert r.status_code == 409 and db_orders()[0].order_status == current  # unchanged
        if target == "CANCELLED":
            assert "cannot be cancelled" in r.json()["message"]
        elif current != target:
            assert "Invalid status change" in r.json()["message"] and "Allowed from" in r.json()["message"]
        else:
            assert "already" in r.json()["message"]


def test_status_request_validation(client, catalog):
    o = place(client)
    for payload in ({}, {"status": "SHIPPING"}, {"status": "pending"}, {"status": 1}, {"status": "CONFIRMED", "payment_status": "PAID"}):
        assert client.patch(f"{URL}{o['id']}/status", json=payload, headers=ADMIN).status_code == 422
    assert client.patch(f"{URL}999/status", json={"status": "CONFIRMED"}, headers=ADMIN).status_code == 404


def test_mandatory_15_cancel_eligible_order_and_16_stock_is_restored(client, catalog):
    o = place(client)
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99)
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    assert r.status_code == 200 and r.json()["order"]["order_status"] == "CANCELLED" and r.json()["stock_restored_now"] == 2
    assert r.json()["message"] == "Order cancelled and the reserved stock was restored"
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # inventory restored
    assert db_orders()[0].order_status == "CANCELLED" and [i.stock_status for i in db_items()] == ["RELEASED", "RELEASED"]
    assert set(catalog.ops) >= {f"{o['order_number']}:release:1", f"{o['order_number']}:release:2"}


@pytest.mark.parametrize("state", ["PENDING", "CONFIRMED", "PROCESSING"])
def test_cancel_allowed_in_early_statuses(client, catalog, state):
    o = place(client)
    advance(client, o["id"], state)
    assert client.post(f"{URL}{o['id']}/cancel", headers=ALICE).status_code == 200 and catalog.stock(1) == 10


@pytest.mark.parametrize("state", ["SHIPPED", "DELIVERED"])
def test_mandatory_17_cannot_cancel_shipped_or_delivered(client, catalog, state):
    o = place(client)
    advance(client, o["id"], state)
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    assert r.status_code == 409 and r.json()["message"] == f"Order cannot be cancelled: it is already {state}"
    assert client.post(f"{URL}{o['id']}/cancel", headers=ADMIN).status_code == 409
    assert db_orders()[0].order_status == state and catalog.stock(1) == 8  # nothing restored


def test_mandatory_18_repeated_cancellation_never_restores_stock_twice(client, catalog):
    o = place(client)
    first = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    releases_after_first = catalog.count("POST", "stock-in")
    assert first.json()["stock_restored_now"] == 2 and catalog.stock(1) == 10
    for _ in range(3):
        again = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
        assert again.status_code == 200 and again.json()["stock_restored_now"] == 0
        assert again.json()["message"] == "Order is cancelled; no stock was restored again"
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # still exactly the original stock
    assert catalog.count("POST", "stock-in") == releases_after_first  # the catalog was not even called again


def test_cancel_via_status_endpoint_also_restores_stock_once(client, catalog):
    o = place(client)
    assert client.patch(f"{URL}{o['id']}/status", json={"status": "CANCELLED"}, headers=ADMIN).json()["order_status"] == "CANCELLED"
    client.patch(f"{URL}{o['id']}/status", json={"status": "CANCELLED"}, headers=ADMIN)  # second attempt: conflict or no-op, never a second restore
    client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)


def test_cancel_unknown_order_and_no_token(client, catalog):
    assert client.post(f"{URL}999/cancel", headers=ALICE).status_code == 404
    assert client.post(f"{URL}1/cancel").status_code == 401


def test_admin_can_cancel_any_order(client, catalog):
    o = place(client)
    assert client.post(f"{URL}{o['id']}/cancel", headers=ADMIN).status_code == 200 and catalog.stock(1) == 10


def test_cancel_with_catalog_down_then_retry_completes_without_double_restore(client, catalog):
    o = place(client)
    catalog.fail(lambda r: "stock-in" in r.url.path, "down")
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    assert r.status_code == 503 and "Repeat the cancel request" in r.json()["message"]
    assert db_orders()[0].order_status == "CANCELLED" and [i.stock_status for i in db_items()] == ["RESERVED", "RESERVED"]  # honest state
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99)
    catalog.clear_faults()
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)  # the retry finishes the job
    assert r.status_code == 200 and r.json()["stock_restored_now"] == 2 and (catalog.stock(1), catalog.stock(2)) == (10, 100)
    assert client.post(f"{URL}{o['id']}/cancel", headers=ALICE).json()["stock_restored_now"] == 0
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)


def test_partial_release_failure_restores_only_what_is_left(client, catalog):
    o = place(client)
    catalog.fail(lambda r: "stock-in" in r.url.path and "/2/" in r.url.path, "down")  # only product 2 fails
    assert client.post(f"{URL}{o['id']}/cancel", headers=ALICE).status_code == 503
    assert [i.stock_status for i in db_items()] == ["RELEASED", "RESERVED"] and (catalog.stock(1), catalog.stock(2)) == (10, 99)
    catalog.clear_faults()
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)
    assert r.json()["stock_restored_now"] == 1 and (catalog.stock(1), catalog.stock(2)) == (10, 100)  # product 1 was NOT restored again


def test_lost_answer_on_release_is_safe_because_the_reference_is_idempotent(client, catalog):
    o = place(client)
    catalog.fail(lambda r: "stock-in" in r.url.path and "/1/" in r.url.path, "timeout_after", times=1)  # applied, but the answer is lost
    r = client.post(f"{URL}{o['id']}/cancel", headers=ALICE)  # the client retries; the same reference is answered "already applied"
    assert r.status_code == 200 and r.json()["stock_restored_now"] == 2
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # restored exactly once although the first call "failed"
    assert catalog.count("POST", "/1/stock-in") == 2


def test_simultaneous_cancellations_restore_stock_once(file_db_client, catalog):
    client = file_db_client
    o = place(client)
    results = [None] * 8
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        results[i] = TestClient(app).post(f"{URL}{o['id']}/cancel", headers=ALICE).status_code

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(results) == {200}
    assert (catalog.stock(1), catalog.stock(2)) == (10, 100)  # restored once: every release carries the same reference
    assert len([r for r in catalog.ops if ":release:" in r]) == 2
