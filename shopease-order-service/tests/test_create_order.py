"""Order creation. MANDATORY n = scenario n of the task."""
from decimal import Decimal

import pytest

from tests.conftest import ADMIN, ALICE, BOB, body, db_compensations, db_items, db_orders, headers, place

URL = "/api/orders/"


def test_mandatory_1_create_order_with_valid_products(client, catalog):
    r = client.post(URL, json=body(notes="  Please deliver   during evening hours "), headers=ALICE)
    assert r.status_code == 201
    o = r.json()
    # Smart Phone X: 20000 x 2, discount 2000 x 2 = 4000; USB Cable: 299 x 1, discount 100
    assert Decimal(o["total_amount"]) == Decimal(o["subtotal"]) - Decimal(o["discount_amount"]) + Decimal(o["tax_amount"]) + Decimal(o["shipping_charge"])


def test_totals_exact(client, catalog):
    o = place(client, notes="  Please deliver   during evening hours ")
    assert (o["subtotal"], o["discount_amount"], o["shipping_charge"], o["tax_amount"], o["total_amount"]) == \
           ("40299.00", "4100.00", "0.00", "6515.82", "42714.82")  # 36199.00 x 18% = 6515.82, free shipping above 1000
    assert o["order_status"] == "PENDING" and o["payment_status"] == "PENDING" and o["user_id"] == 11
    assert o["order_number"].startswith("ORD-") and o["notes"] == "Please deliver during evening hours"
    assert [(i["product_id"], i["product_name"], i["sku"], i["unit_price"], i["quantity"], i["discount_amount"], i["total_price"], i["stock_status"]) for i in o["items"]] == \
           [(1, "Smart Phone X", "SKU-1", "20000.00", 2, "4000.00", "36000.00", "RESERVED"), (2, "USB Cable", "SKU-2", "299.00", 1, "100.00", "199.00", "RESERVED")]
    assert o["shipping_address"]["id"] == 1 and o["shipping_address"]["city"] == "Hyderabad" and o["shipping_address"]["address_line_1"] == "12 Rose Street"
    # database records
    row = db_orders()[0]
    assert (row.order_number, row.user_id, row.total_amount, row.order_status) == (o["order_number"], 11, Decimal("42714.82"), "PENDING")
    assert [(i.product_id, i.quantity, i.stock_status) for i in db_items()] == [(1, 2, "RESERVED"), (2, 1, "RESERVED")]
    assert sum(i.total_price for i in db_items()) == row.subtotal - row.discount_amount  # order total matches its items
    # inventory was reserved in the catalog with unique references
    assert (catalog.stock(1), catalog.stock(2)) == (8, 99)
    assert set(catalog.ops) == {f"{o['order_number']}:reserve:1", f"{o['order_number']}:reserve:2"}


def test_shipping_charge_below_threshold_and_tax_rounding(client, catalog):
    catalog.add(5, "Pen", "99.99", 50)
    o = place(client, items=[{"product_id": 5, "quantity": 3}])  # 299.97 < 1000 -> 50.00 shipping; tax 53.9946 -> 53.99
    assert (o["subtotal"], o["discount_amount"], o["tax_amount"], o["shipping_charge"], o["total_amount"]) == ("299.97", "0.00", "53.99", "50.00", "403.96")
    catalog.add(6, "Edge", "1000.00", 5)  # exactly at the threshold ships free
    assert place(client, items=[{"product_id": 6, "quantity": 1}])["shipping_charge"] == "0.00"
    catalog.add(7, "Half", "0.05", 5)  # 0.05 x 0.18 = 0.009 -> 0.01 (round half up, no float errors)
    assert place(client, items=[{"product_id": 7, "quantity": 1}])["tax_amount"] == "0.01"


def test_snapshots_survive_catalog_changes(client, catalog):
    o = place(client)
    catalog.products[1].update(name="Renamed Phone", price="99999.00", sku="NEW-SKU")
    again = client.get(f"{URL}{o['id']}", headers=ALICE).json()
    assert again["items"][0]["product_name"] == "Smart Phone X" and again["items"][0]["unit_price"] == "20000.00" and again["items"][0]["sku"] == "SKU-1"
    assert again["total_amount"] == o["total_amount"]


def test_mandatory_2_invalid_product_id(client, catalog):
    r = client.post(URL, json=body(items=[{"product_id": 1, "quantity": 1}, {"product_id": 999, "quantity": 1}]), headers=ALICE)
    assert r.status_code == 422 and r.json()["message"] == "Product 999 does not exist"
    assert db_orders() == [] and (catalog.stock(1), catalog.count("POST", "stock-out")) == (10, 0)  # nothing reserved


def test_mandatory_3_inactive_product(client, catalog):
    r = client.post(URL, json=body(items=[{"product_id": 3, "quantity": 1}]), headers=ALICE)
    assert r.status_code == 422 and "not available for ordering" in r.json()["message"] and "Old Phone" in r.json()["message"]
    assert db_orders() == [] and catalog.stock(3) == 5


def test_mandatory_4_insufficient_stock(client, catalog):
    r = client.post(URL, json=body(items=[{"product_id": 1, "quantity": 2}, {"product_id": 4, "quantity": 4}]), headers=ALICE)
    assert r.status_code == 409 and r.json()["message"] == "Insufficient stock for Laptop Pro: 3 available, 4 requested"
    assert db_orders() == [] and (catalog.stock(1), catalog.stock(4)) == (10, 3)
    # exactly the available quantity works
    assert place(client, items=[{"product_id": 4, "quantity": 3}])["items"][0]["quantity"] == 3 and catalog.stock(4) == 0


@pytest.mark.parametrize("address", [999, 3])  # unknown id, and an address that belongs to Bob
def test_mandatory_5_invalid_shipping_address(client, catalog, address):
    r = client.post(URL, json=body(address=address), headers=ALICE)
    assert r.status_code == 422 and "not found among your saved addresses" in r.json()["message"]
    assert db_orders() == [] and catalog.count("POST", "stock-out") == 0


def test_another_users_address_is_never_accepted_even_if_the_user_service_returned_it(client, catalog, users):
    users.addresses[3]["user_id"] = 101  # a misbehaving User Service would still not fool the ownership check below
    users.profiles[11]["id"] = 555
    assert client.post(URL, json=body(address=3), headers=ALICE).status_code == 422


def test_mandatory_6_no_token(client, catalog):
    r = client.post(URL, json=body())
    assert r.status_code == 401 and r.json()["message"] == "Not authenticated: Bearer token is missing"
    assert db_orders() == [] and catalog.calls == []


@pytest.mark.parametrize("token", ["garbage", "a.b.c"])
def test_mandatory_7_invalid_token(client, catalog, token):
    r = client.post(URL, json=body(), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401 and r.json()["message"] == "Invalid token" and db_orders() == []
    assert client.post(URL, json=body(), headers=headers(11, "customer", expires_in=-10)).json()["message"] == "Token has expired"


def test_owner_is_taken_from_the_token_and_body_ids_are_rejected(client, catalog):
    o = place(client, who=BOB, address=3)
    assert o["user_id"] == 12 and db_orders()[0].user_id == 12
    for extra in ({"user_id": 11}, {"unit_price": "1.00"}, {"total_amount": "1.00"}, {"discount_amount": "9999"}, {"tax_amount": "0"},
                  {"order_status": "DELIVERED"}, {"payment_status": "PAID"}):
        r = client.post(URL, json=body(address=3, **extra), headers=BOB)
        assert r.status_code == 422 and r.json()["details"][0]["message"] == "Field is not allowed", extra
    bad_item = {"items": [{"product_id": 1, "quantity": 1, "unit_price": "1.00"}], "shipping_address_id": 3}
    assert client.post(URL, json=bad_item, headers=BOB).status_code == 422
    assert len(db_orders()) == 1


@pytest.mark.parametrize("payload", [
    {"items": [], "shipping_address_id": 1}, {"shipping_address_id": 1}, {"items": [{"product_id": 1, "quantity": 1}]},
    {"items": [{"product_id": 1, "quantity": 0}], "shipping_address_id": 1}, {"items": [{"product_id": 1, "quantity": -2}], "shipping_address_id": 1},
    {"items": [{"product_id": 1, "quantity": 101}], "shipping_address_id": 1}, {"items": [{"product_id": 1, "quantity": 1.5}], "shipping_address_id": 1},
    {"items": [{"product_id": 0, "quantity": 1}], "shipping_address_id": 1}, {"items": [{"product_id": "x", "quantity": 1}], "shipping_address_id": 1},
    {"items": [{"product_id": 1, "quantity": 1}, {"product_id": 1, "quantity": 2}], "shipping_address_id": 1},
    {"items": [{"product_id": 1, "quantity": 1}], "shipping_address_id": 0}, {"items": [{"product_id": 1, "quantity": 1}], "shipping_address_id": "a"},
    {"items": [{"product_id": 1, "quantity": 1}], "shipping_address_id": 1, "notes": "x" * 501},
    {"items": [{"product_id": i, "quantity": 1} for i in range(1, 22)], "shipping_address_id": 1},
])
def test_invalid_request_bodies(client, catalog, payload):
    r = client.post(URL, json=payload, headers=ALICE)
    assert r.status_code == 422 and r.json()["error"] == "Validation Error" and db_orders() == [] and catalog.calls == []


def test_profile_rules(client, catalog, users):
    users.profiles.pop(11)
    r = client.post(URL, json=body(), headers=ALICE)
    assert r.status_code == 422 and "profile is required" in r.json()["message"]
    users.profiles[11] = {"id": 101, "auth_user_id": 11, "full_name": "Alice", "phone_number": "9", "is_active": False}
    assert client.post(URL, json=body(), headers=ALICE).status_code == 403
    assert db_orders() == [] and catalog.calls == []


def test_admin_can_also_place_an_order_for_themselves(client, catalog, users):
    users.addresses[9] = {**users.addresses[1], "id": 9, "user_id": 100}
    o = place(client, who=ADMIN, address=9)
    assert o["user_id"] == 1


def test_database_constraints(client, catalog):
    from sqlalchemy.exc import IntegrityError
    from app.database.connection import SessionLocal
    from app.models.order_item_model import OrderItem
    from app.models.order_model import Order
    o = place(client)
    base = dict(user_id=1, shipping_address={}, subtotal=Decimal("10.00"), discount_amount=Decimal("0"), shipping_charge=Decimal("0"),
                tax_amount=Decimal("0"), total_amount=Decimal("10.00"), order_status="PENDING", payment_status="PENDING")
    bad = [dict(order_number=o["order_number"]), dict(order_number="X1", order_status="WEIRD"), dict(order_number="X2", total_amount=Decimal("11.00")),
           dict(order_number="X3", subtotal=Decimal("-1"), total_amount=Decimal("-1")), dict(order_number="X4", payment_status="NOPE")]
    for override in bad:
        with SessionLocal() as db:
            db.add(Order(**{**base, **override}))
            with pytest.raises(IntegrityError):
                db.commit()
    for override in (dict(quantity=0), dict(total_price=Decimal("5.00")), dict(unit_price=Decimal("-1")), dict(order_id=9999), dict(stock_status="LOST")):
        item = dict(order_id=1, product_id=1, product_name="n", sku="s", unit_price=Decimal("10.00"), quantity=1, discount_amount=Decimal("0"), total_price=Decimal("10.00"))
        with SessionLocal() as db:
            db.add(OrderItem(**{**item, **override}))
            with pytest.raises(IntegrityError):
                db.commit()
    assert len(db_orders()) == 1 and len(db_items()) == 2
