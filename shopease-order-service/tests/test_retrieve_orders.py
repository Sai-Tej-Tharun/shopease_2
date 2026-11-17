"""Order retrieval, pagination, filtering and order items. MANDATORY n = scenario n of the task."""
from datetime import datetime, timedelta

import pytest

from app.database.connection import SessionLocal
from app.models.order_model import Order
from tests.conftest import ADMIN, ALICE, BOB, db_orders, headers, place

URL = "/api/orders/"


@pytest.fixture
def orders(client, catalog):
    """Alice: 4 orders (ids 1-4), Bob: 1 order (id 5)."""
    catalog.add(5, "Pen", "20.00", 500)
    made = [place(client, items=[{"product_id": 5, "quantity": n}]) for n in (1, 2, 3, 4)]
    made.append(place(client, who=BOB, address=3, items=[{"product_id": 5, "quantity": 5}]))
    return made


def test_mandatory_8_user_lists_own_orders(client, orders):
    r = client.get(URL, headers=ALICE)
    assert r.status_code == 200 and r.json()["total"] == 4
    assert [o["id"] for o in r.json()["items"]] == [4, 3, 2, 1]  # newest first
    assert all(o["user_id"] == 11 and o["items"] for o in r.json()["items"])
    assert client.get(URL, headers=BOB).json()["total"] == 1
    assert client.get(URL, headers=headers(77, "customer")).json() == {"items": [], "total": 0, "page": 1, "page_size": 10, "total_pages": 0}


def test_mandatory_9_cannot_read_another_users_order(client, orders):
    for url in (f"{URL}5", f"{URL}number/{orders[4]['order_number']}", f"{URL}5/items", f"{URL}5/items/5"):
        r = client.get(url, headers=ALICE)
        assert r.status_code == 403 and r.json()["message"] == "You do not have access to this order", url
    assert client.post(f"{URL}5/cancel", headers=ALICE).status_code == 403
    assert client.get(URL + "?user_id=12", headers=ALICE).status_code == 403  # a customer cannot list another user's orders
    assert client.get(URL + "?user_id=11", headers=ALICE).json()["total"] == 4  # their own id is fine


def test_get_order_details_and_unknown_order(client, orders):
    r = client.get(f"{URL}1", headers=ALICE)
    assert r.status_code == 200 and r.json() == orders[0]
    r = client.get(f"{URL}999", headers=ALICE)
    assert r.status_code == 404 and r.json()["message"] == "Order with id 999 not found"
    assert client.get(f"{URL}abc", headers=ALICE).status_code == 422
    assert client.get(f"{URL}0", headers=ADMIN).status_code == 404


def test_mandatory_10_get_order_by_number(client, orders):
    number = orders[1]["order_number"]
    r = client.get(f"{URL}number/{number}", headers=ALICE)
    assert r.status_code == 200 and r.json()["id"] == 2 and r.json()["order_number"] == number
    assert client.get(f"{URL}number/{number.lower()}", headers=ALICE).json()["id"] == 2  # case-insensitive
    r = client.get(f"{URL}number/ORD-NOPE", headers=ALICE)
    assert r.status_code == 404 and "not found" in r.json()["message"]
    assert client.get(f"{URL}number/{number}", headers=ADMIN).status_code == 200


def test_mandatory_11_pagination(client, orders):
    r = client.get(URL + "?page=1&page_size=3", headers=ALICE).json()
    assert (r["total"], r["page"], r["page_size"], r["total_pages"]) == (4, 1, 3, 2) and [o["id"] for o in r["items"]] == [4, 3, 2]
    assert [o["id"] for o in client.get(URL + "?page=2&page_size=3", headers=ALICE).json()["items"]] == [1]
    assert client.get(URL + "?page=5&page_size=3", headers=ALICE).json()["items"] == []
    assert client.get(URL, headers=ALICE).json()["page_size"] == 10
    for q in ("page=0", "page_size=0", "page_size=51", "page=x", "status=SHIPPING", "date_from=2026-13-01", "user_id=0"):
        assert client.get(URL + "?" + q, headers=ALICE).status_code == 422, q


def test_mandatory_11_filter_by_status(client, orders):
    client.patch(f"{URL}1/status", json={"status": "CONFIRMED"}, headers=ADMIN)
    client.patch(f"{URL}2/status", json={"status": "CONFIRMED"}, headers=ADMIN)
    client.post(f"{URL}3/cancel", headers=ALICE)
    ids = lambda q, who=ALICE: sorted(o["id"] for o in client.get(URL + q, headers=who).json()["items"])
    assert ids("?status=CONFIRMED") == [1, 2] and ids("?status=CANCELLED") == [3] and ids("?status=PENDING") == [4]
    assert ids("?status=SHIPPED") == [] and client.get(URL + "?status=CONFIRMED&page_size=1", headers=ALICE).json()["total"] == 2


def test_mandatory_11_filter_by_date_range(client, orders):
    now = datetime.utcnow()
    with SessionLocal() as db:  # spread the orders over different days
        for order_id, days_ago in ((1, 10), (2, 5), (3, 2), (4, 0)):
            db.get(Order, order_id).created_at = now - timedelta(days=days_ago)
        db.commit()
    d = lambda days: (now - timedelta(days=days)).strftime("%Y-%m-%d")
    ids = lambda q: sorted(o["id"] for o in client.get(URL + q, headers=ALICE).json()["items"])
    assert ids(f"?date_from={d(5)}") == [2, 3, 4]
    assert ids(f"?date_to={d(5)}") == [1, 2]  # the end date is inclusive
    assert ids(f"?date_from={d(6)}&date_to={d(1)}") == [2, 3]
    assert ids(f"?date_from={d(0)}&date_to={d(0)}") == [4]
    assert ids(f"?date_from={d(30)}&date_to={d(20)}") == []
    assert ids(f"?date_from={d(5)}&status=PENDING") == [2, 3, 4]
    r = client.get(URL + f"?date_from={d(1)}&date_to={d(5)}", headers=ALICE)
    assert r.status_code == 422 and "date_from must not be after date_to" in r.json()["message"]


def test_administrator_sees_all_orders(client, orders):
    r = client.get(URL, headers=ADMIN).json()
    assert r["total"] == 5 and [o["id"] for o in r["items"]] == [5, 4, 3, 2, 1]
    assert client.get(URL + "?user_id=12", headers=ADMIN).json()["total"] == 1
    assert client.get(f"{URL}5", headers=ADMIN).status_code == 200


def test_order_items_endpoints(client, orders):
    r = client.get(f"{URL}1/items", headers=ALICE)
    assert r.status_code == 200 and r.json()["order_number"] == orders[0]["order_number"] and r.json()["total_items"] == 1
    item = r.json()["items"][0]
    assert (item["product_name"], item["sku"], item["unit_price"], item["quantity"], item["total_price"]) == ("Pen", "SKU-5", "20.00", 1, "20.00")
    assert client.get(f"{URL}1/items/{item['id']}", headers=ALICE).json() == item
    assert client.get(f"{URL}1/items/999", headers=ALICE).status_code == 404
    assert client.get(f"{URL}1/items/{orders[1]['items'][0]['id']}", headers=ALICE).status_code == 404  # an item of another order
    assert client.get(f"{URL}999/items", headers=ALICE).status_code == 404
    assert client.get(f"{URL}5/items", headers=ADMIN).status_code == 200


def test_no_token_on_read_endpoints(client, orders):
    for url in (URL, f"{URL}1", f"{URL}number/{orders[0]['order_number']}", f"{URL}1/items", f"{URL}compensations"):
        assert client.get(url).status_code == 401, url
