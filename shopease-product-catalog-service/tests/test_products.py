"""Product APIs, search, filtering, pagination and sorting. MANDATORY n = scenario n of the task."""
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.connection import SessionLocal
from app.models.product_model import Product
from tests.conftest import ADMIN, CUSTOMER, add_product, db_products, db_txns, product_body, put_body

URL = "/api/products/"


# ====================================================== create (scenarios 1-4)
def test_mandatory_1_create_product(client, category):
    r = client.post(URL, json=product_body(), headers=ADMIN)
    assert r.status_code == 201
    b = r.json()
    assert b["sku"] == "PH-001" and Decimal(b["price"]) == Decimal("20000.00") and Decimal(b["discount_price"]) == Decimal("18000.00")
    assert Decimal(b["final_price"]) == Decimal("18000.00") and b["in_stock"] is True and b["stock_quantity"] == 50
    assert b["category"] == {"id": 1, "name": "Electronics"} and b["is_active"] is True and b["brand"] == "Acme"
    row = db_products()[0]  # database check
    assert (row.sku, row.price, row.discount_price, row.stock_quantity) == ("PH-001", Decimal("20000.00"), Decimal("18000.00"), 50)
    tx = db_txns(1)  # the initial stock is recorded as the first inventory transaction
    assert len(tx) == 1 and (tx[0].transaction_type, tx[0].quantity, tx[0].previous_stock, tx[0].updated_stock, tx[0].remarks) == ("IN", 50, 0, 50, "Initial stock")


def test_product_with_zero_stock_has_no_initial_transaction(client, category):
    p = add_product(client, stock_quantity=0)
    assert p["in_stock"] is False and db_txns(p["id"]) == []


def test_minimal_product(client, category):
    r = client.post(URL, json={"category_id": 1, "name": "Cable", "sku": "cb-1", "price": 99.5}, headers=ADMIN)
    assert r.status_code == 201
    assert r.json()["sku"] == "CB-1" and r.json()["price"] == "99.50" and r.json()["discount_price"] is None
    assert Decimal(r.json()["final_price"]) == Decimal("99.50") and r.json()["stock_quantity"] == 0


def test_mandatory_2_duplicate_sku(client, product):
    for sku in ("PH-001", "ph-001", " Ph-001 "):
        r = client.post(URL, json=product_body(name="Other", sku=sku), headers=ADMIN)
        assert r.status_code == 409 and r.json()["message"] == "A product with SKU 'PH-001' already exists"
    assert len(db_products()) == 1


def test_mandatory_3_invalid_category(client, category):
    r = client.post(URL, json=product_body(category_id=999), headers=ADMIN)
    assert r.status_code == 422 and r.json()["message"] == "Category with id 999 does not exist"
    assert client.post(URL, json=product_body(category_id=0), headers=ADMIN).status_code == 422
    assert client.post(URL, json=product_body(category_id="abc"), headers=ADMIN).status_code == 422
    assert db_products() == []


def test_product_in_inactive_category_is_rejected(client, category):
    client.put("/api/categories/1", json={"name": "Electronics", "is_active": False}, headers=ADMIN)
    r = client.post(URL, json=product_body(), headers=ADMIN)
    assert r.status_code == 422 and "inactive" in r.json()["message"]


@pytest.mark.parametrize("field,value,message", [
    ("price", "-5", "Price cannot be negative"), ("price", -0.01, "Price cannot be negative"), ("price", "0", "Price must be greater than 0"),
    ("price", "10.999", "Price must have at most 2 decimal places"), ("price", "abc", None), ("price", None, None),
    ("price", "1000000000", "Price must not exceed"), ("discount_price", "-1", "Discount price cannot be negative"),
    ("discount_price", "20000", "Discount price must be lower than the price"), ("discount_price", "25000", "Discount price must be lower than the price"),
    ("stock_quantity", -1, "Stock quantity cannot be negative"), ("stock_quantity", "abc", None),
])
def test_mandatory_4_invalid_prices_and_stock(client, category, field, value, message):
    r = client.post(URL, json=product_body(**{field: value}), headers=ADMIN)
    assert r.status_code == 422
    text = " ".join(d["message"] for d in r.json()["details"])
    if message:
        assert message in text
    assert db_products() == []


@pytest.mark.parametrize("field,value", [("name", ""), ("name", "A"), ("name", "<b>x</b>"), ("name", "x" * 151), ("sku", "A"), ("sku", "bad sku!"),
                                         ("sku", "-ABC"), ("image_url", "ftp://x"), ("image_url", "javascript:alert(1)"),
                                         ("brand", "x" * 101), ("is_active", "maybe")])
def test_other_invalid_fields(client, category, field, value):
    r = client.post(URL, json=product_body(**{field: value}), headers=ADMIN)
    assert r.status_code == 422 and r.json()["details"][0]["field"] == field


def test_required_fields_and_unknown_fields(client, category):
    r = client.post(URL, json={}, headers=ADMIN)
    assert {d["field"] for d in r.json()["details"]} == {"category_id", "name", "sku", "price"}
    r = client.post(URL, json=product_body(id=5, created_at="2020-01-01"), headers=ADMIN)
    assert r.status_code == 422 and all(d["message"] == "Field is not allowed" for d in r.json()["details"])


def test_validation_errors_do_not_echo_values(client, category):
    r = client.post(URL, json=product_body(price="SECRET-VALUE"), headers=ADMIN)
    assert "SECRET-VALUE" not in r.text


# ====================================================== read (scenarios 5, 6)
def test_mandatory_5_customer_retrieves_all_active_products(client, category):
    add_product(client, sku="A-1", name="Active One")
    add_product(client, sku="I-1", name="Inactive One", is_active=False)
    add_product(client, sku="A-2", name="Active Two")
    r = client.get(URL, headers=CUSTOMER)
    assert r.status_code == 200 and r.json()["total"] == 2
    assert [p["sku"] for p in r.json()["items"]] == ["A-1", "A-2"]  # inactive products are not exposed
    assert client.get(URL + "?is_active=false", headers=CUSTOMER).json()["total"] == 2  # customers cannot ask for inactive ones
    admin_all = client.get(URL, headers=ADMIN).json()
    assert admin_all["total"] == 3
    assert [p["sku"] for p in client.get(URL + "?is_active=true", headers=ADMIN).json()["items"]] == ["A-1", "A-2"]
    assert [p["sku"] for p in client.get(URL + "?is_active=false", headers=ADMIN).json()["items"]] == ["I-1"]


def test_get_product_details(client, product):
    r = client.get(f"{URL}{product['id']}", headers=CUSTOMER)
    assert r.status_code == 200 and r.json() == product


def test_mandatory_6_invalid_product_id(client, product):
    for call in (lambda: client.get(f"{URL}9999", headers=CUSTOMER), lambda: client.get(f"{URL}0", headers=ADMIN),
                 lambda: client.put(f"{URL}9999", json=put_body(sku="Z-9"), headers=ADMIN),
                 lambda: client.patch(f"{URL}9999", json={"name": "Nope"}, headers=ADMIN),
                 lambda: client.delete(f"{URL}9999", headers=ADMIN)):
        r = call()
        assert r.status_code == 404 and r.json()["error"] == "Not Found" and "not found" in r.json()["message"]
    assert client.get(f"{URL}abc", headers=CUSTOMER).status_code == 422


def test_inactive_product_is_hidden_from_customers_but_not_admins(client, product):
    client.patch(f"{URL}{product['id']}", json={"is_active": False}, headers=ADMIN)
    assert client.get(f"{URL}{product['id']}", headers=CUSTOMER).status_code == 404
    assert client.get(f"{URL}{product['id']}", headers=ADMIN).json()["is_active"] is False


# ====================================================== update / patch / delete (scenarios 7, 8)
def test_mandatory_7_update_product(client, product):
    new = put_body(name="Smart Phone X Pro", sku="ph-002", price="25000.00", discount_price=None, brand="Acme Pro", description=None)
    r = client.put(f"{URL}{product['id']}", json=new, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["name"] == "Smart Phone X Pro" and r.json()["sku"] == "PH-002" and r.json()["discount_price"] is None
    assert Decimal(r.json()["final_price"]) == Decimal("25000.00") and r.json()["stock_quantity"] == 50  # stock untouched
    row = db_products()[0]
    assert (row.name, row.sku, row.price, row.discount_price, row.description) == ("Smart Phone X Pro", "PH-002", Decimal("25000.00"), None, None)
    assert client.put(f"{URL}{product['id']}", json={"name": "only"}, headers=ADMIN).status_code == 422


def test_put_keeping_own_sku_is_fine_but_foreign_sku_conflicts(client, product):
    other = add_product(client, sku="OT-1", name="Other")
    assert client.put(f"{URL}{product['id']}", json=put_body(), headers=ADMIN).status_code == 200
    assert client.put(f"{URL}{product['id']}", json=put_body(sku="OT-1"), headers=ADMIN).status_code == 409


def test_mandatory_7_partial_update_product(client, product):
    r = client.patch(f"{URL}{product['id']}", json={"price": "21000.00", "brand": "Acme Plus"}, headers=ADMIN)
    assert r.status_code == 200 and Decimal(r.json()["price"]) == Decimal("21000.00") and r.json()["brand"] == "Acme Plus"
    assert r.json()["name"] == "Smart Phone X" and Decimal(r.json()["discount_price"]) == Decimal("18000.00")  # untouched
    row = db_products()[0]
    assert row.price == Decimal("21000.00") and row.brand == "Acme Plus" and row.sku == "PH-001"


def test_patch_rules(client, product):
    pid = product["id"]
    assert client.patch(f"{URL}{pid}", json={}, headers=ADMIN).status_code == 422
    for field in ("name", "sku", "price", "category_id", "is_active"):
        assert client.patch(f"{URL}{pid}", json={field: None}, headers=ADMIN).status_code == 422, field
    r = client.patch(f"{URL}{pid}", json={"price": "15000.00"}, headers=ADMIN)  # would drop below the existing discount
    assert r.status_code == 422 and "Discount price must be lower" in r.json()["message"]
    assert client.patch(f"{URL}{pid}", json={"discount_price": "99999.00"}, headers=ADMIN).status_code == 422
    assert Decimal(client.get(f"{URL}{pid}", headers=ADMIN).json()["price"]) == Decimal("20000.00")  # unchanged
    r = client.patch(f"{URL}{pid}", json={"discount_price": None, "description": None, "image_url": None}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["discount_price"] is None and r.json()["description"] is None
    assert client.patch(f"{URL}{pid}", json={"price": "-1"}, headers=ADMIN).status_code == 422
    assert client.patch(f"{URL}{pid}", json={"sku": "x"}, headers=ADMIN).status_code == 422
    add_product(client, sku="OT-1", name="Other")
    assert client.patch(f"{URL}{pid}", json={"sku": "ot-1"}, headers=ADMIN).status_code == 409


def test_stock_cannot_be_changed_through_update_or_patch(client, product):
    for call in (lambda: client.patch(f"{URL}{product['id']}", json={"stock_quantity": 5}, headers=ADMIN),
                 lambda: client.put(f"{URL}{product['id']}", json=product_body(stock_quantity=5), headers=ADMIN)):
        r = call()
        assert r.status_code == 422 and "inventory stock-in and stock-out" in r.text
    assert db_products()[0].stock_quantity == 50


def test_patch_category_rules(client, product):
    assert client.patch(f"{URL}{product['id']}", json={"category_id": 999}, headers=ADMIN).status_code == 422
    client.post("/api/categories/", json={"name": "Off", "is_active": False}, headers=ADMIN)
    assert client.patch(f"{URL}{product['id']}", json={"category_id": 2}, headers=ADMIN).status_code == 422
    client.post("/api/categories/", json={"name": "Phones"}, headers=ADMIN)
    r = client.patch(f"{URL}{product['id']}", json={"category_id": 3}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["category"]["name"] == "Phones"


def test_mandatory_8_deactivate_and_activate_product(client, product):
    r = client.patch(f"{URL}{product['id']}", json={"is_active": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["is_active"] is False and db_products()[0].is_active is False
    assert client.get(URL, headers=CUSTOMER).json()["total"] == 0
    assert client.patch(f"{URL}{product['id']}", json={"is_active": True}, headers=ADMIN).json()["is_active"] is True
    assert client.get(URL, headers=CUSTOMER).json()["total"] == 1


def test_mandatory_8_delete_product_and_its_history(client, product):
    client.post(f"/api/inventory/{product['id']}/stock-out", json={"quantity": 5}, headers=ADMIN)
    assert len(db_txns()) == 2
    r = client.delete(f"{URL}{product['id']}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["message"] == "Product deleted successfully"
    assert db_products() == [] and db_txns() == []  # history is removed with the product
    assert client.get(f"{URL}{product['id']}", headers=ADMIN).status_code == 404


# ====================================================== search, filter, pagination, sorting (scenarios 10, 11)
@pytest.fixture
def catalog(client, category):
    client.post("/api/categories/", json={"name": "Accessories"}, headers=ADMIN)
    data = [  # name, sku, category, price, discount, stock, active
        ("Smart Phone X", "PH-001", 1, "20000.00", "18000.00", 50, True),
        ("Smart Phone Mini", "PH-002", 1, "9000.00", None, 0, True),
        ("Laptop Pro", "LT-100", 1, "60000.00", "55000.00", 5, True),
        ("Phone Charger", "AC-010", 2, "799.00", None, 200, True),
        ("USB Cable", "AC-011", 2, "299.00", "199.00", 120, True),
        ("Old Phone", "PH-009", 1, "3000.00", None, 8, False),
        ("Cotton Case", "AC_099", 2, "500.00", None, 15, True),
    ]
    for name, sku, cat, price, disc, stock, active in data:
        add_product(client, name=name, sku=sku, category_id=cat, price=price, discount_price=disc, stock_quantity=stock, is_active=active)


def skus(r):
    assert r.status_code == 200, r.text
    return [p["sku"] for p in r.json()["items"]]


def test_mandatory_10_search(client, catalog):
    assert skus(client.get(URL + "?search=phone", headers=CUSTOMER)) == ["PH-001", "PH-002", "AC-010"]  # name; inactive hidden
    assert skus(client.get(URL + "?search=PHONE", headers=CUSTOMER)) == ["PH-001", "PH-002", "AC-010"]  # case-insensitive
    assert skus(client.get(URL + "?search=lt-1", headers=CUSTOMER)) == ["LT-100"]  # SKU
    assert skus(client.get(URL + "?search=zzz", headers=CUSTOMER)) == []
    assert skus(client.get(URL + "?search=phone", headers=ADMIN)) == ["PH-001", "PH-002", "AC-010", "PH-009"]


def test_search_treats_wildcards_literally(client, catalog):
    assert skus(client.get(URL + "?search=%25", headers=CUSTOMER)) == []  # an unescaped % would match everything
    assert skus(client.get(URL + "?search=_", headers=CUSTOMER)) == ["AC_099"]  # an unescaped _ would match every character
    assert skus(client.get(URL + "?search=c_0", headers=CUSTOMER)) == ["AC_099"]
    assert skus(client.get(URL + "?search=" + "%27%20OR%201%3D1%20--", headers=CUSTOMER)) == []


def test_mandatory_10_filters(client, catalog):
    assert skus(client.get(URL + "?category_id=2", headers=CUSTOMER)) == ["AC-010", "AC-011", "AC_099"]
    assert skus(client.get(URL + "?min_price=1000&max_price=20000", headers=CUSTOMER)) == ["PH-001", "PH-002"]  # selling price (discount applied)
    assert skus(client.get(URL + "?min_price=18000", headers=CUSTOMER)) == ["PH-001", "LT-100"]
    assert skus(client.get(URL + "?max_price=300", headers=CUSTOMER)) == ["AC-011"]
    assert skus(client.get(URL + "?in_stock=false", headers=CUSTOMER)) == ["PH-002"]
    assert skus(client.get(URL + "?in_stock=true&category_id=1", headers=CUSTOMER)) == ["PH-001", "LT-100"]
    assert skus(client.get(URL + "?search=phone&category_id=1&min_price=5000&in_stock=true", headers=CUSTOMER)) == ["PH-001"]
    assert skus(client.get(URL + "?category_id=999", headers=CUSTOMER)) == []


@pytest.mark.parametrize("query", ["min_price=-1", "max_price=abc", "min_price=10&max_price=5", "category_id=0", "category_id=x", "in_stock=maybe",
                                   "sort_by=color", "sort_order=up", "page=0", "page=x", "page_size=0", "page_size=101", "search=", "min_price=1.234"])
def test_invalid_query_parameters(client, catalog, query):
    r = client.get(URL + "?" + query, headers=CUSTOMER)
    assert r.status_code == 422 and r.json()["error"] == "Validation Error"


def test_mandatory_11_pagination(client, catalog):
    r = client.get(URL + "?page=1&page_size=2", headers=CUSTOMER).json()
    assert (r["total"], r["page"], r["page_size"], r["total_pages"]) == (6, 1, 2, 3)
    assert [p["sku"] for p in r["items"]] == ["PH-001", "PH-002"]
    assert skus(client.get(URL + "?page=2&page_size=2", headers=CUSTOMER)) == ["LT-100", "AC-010"]
    assert skus(client.get(URL + "?page=3&page_size=2", headers=CUSTOMER)) == ["AC-011", "AC_099"]
    r = client.get(URL + "?page=4&page_size=2", headers=CUSTOMER).json()
    assert r["items"] == [] and r["total"] == 6
    assert len(client.get(URL, headers=CUSTOMER).json()["items"]) == 6 and client.get(URL, headers=CUSTOMER).json()["page_size"] == 10
    empty = client.get(URL + "?search=zzz", headers=CUSTOMER).json()
    assert (empty["total"], empty["total_pages"], empty["items"]) == (0, 0, [])


def test_mandatory_11_sorting(client, catalog):
    assert skus(client.get(URL + "?sort_by=price&sort_order=asc", headers=CUSTOMER)) == ["AC-011", "AC_099", "AC-010", "PH-002", "PH-001", "LT-100"]
    assert skus(client.get(URL + "?sort_by=price&sort_order=desc", headers=CUSTOMER)) == ["LT-100", "PH-001", "PH-002", "AC-010", "AC_099", "AC-011"]
    assert skus(client.get(URL + "?sort_by=name", headers=CUSTOMER)) == ["AC_099", "LT-100", "AC-010", "PH-002", "PH-001", "AC-011"]
    assert skus(client.get(URL + "?sort_by=stock_quantity&sort_order=desc&page_size=3", headers=CUSTOMER)) == ["AC-010", "AC-011", "PH-001"]
    assert skus(client.get(URL + "?sort_by=id&sort_order=desc&page_size=2", headers=CUSTOMER)) == ["AC_099", "AC-011"]
    assert skus(client.get(URL + "?sort_by=created_at", headers=CUSTOMER))[0] == "PH-001"
    # sorting + paging + filtering together
    assert skus(client.get(URL + "?category_id=1&sort_by=price&sort_order=desc&page=1&page_size=2", headers=CUSTOMER)) == ["LT-100", "PH-001"]


def test_only_the_requested_page_is_loaded(client, catalog):
    r = client.get(URL + "?page_size=1", headers=CUSTOMER).json()
    assert len(r["items"]) == 1 and r["total"] == 6


def test_trailing_slash_redirect_works(client, catalog):
    r = client.get("/api/products?page_size=1", headers=CUSTOMER)  # no trailing slash: redirected, header is kept by the test client
    assert r.status_code in (200, 307)


# ====================================================== database constraints
def test_database_check_constraints(client, product):
    from app.models.inventory_model import InventoryTransaction
    bad_products = [dict(stock_quantity=-1), dict(price=Decimal("0")), dict(price=Decimal("-1")), dict(discount_price=Decimal("30000.00")),
                    dict(discount_price=Decimal("-1.00")), dict(sku="PH-001")]
    for overrides in bad_products:
        values = dict(category_id=1, name="Bad", sku="BAD-1", price=Decimal("10.00"), stock_quantity=1)
        values.update(overrides)
        with SessionLocal() as db:
            db.add(Product(**values))
            with pytest.raises(IntegrityError):
                db.commit()
    bad_tx = [dict(transaction_type="SIDEWAYS"), dict(quantity=0), dict(quantity=-3), dict(updated_stock=-1), dict(previous_stock=-1)]
    for overrides in bad_tx:
        values = dict(product_id=1, transaction_type="IN", quantity=1, previous_stock=0, updated_stock=1)
        values.update(overrides)
        with SessionLocal() as db:
            db.add(InventoryTransaction(**values))
            with pytest.raises(IntegrityError):
                db.commit()
    with SessionLocal() as db:
        db.add(Product(category_id=999, name="Orphan", sku="ORPH-1", price=Decimal("10.00")))
        with pytest.raises(IntegrityError):
            db.commit()
    assert len(db_products()) == 1


def test_money_is_stored_as_decimal_without_float_errors(client, category):
    add_product(client, sku="D-1", name="Decimal", price="0.10", discount_price="0.01", stock_quantity=0)
    add_product(client, sku="D-2", name="Decimal Two", price="19999999.99", discount_price=None, stock_quantity=0)
    rows = {p.sku: p for p in db_products()}
    assert rows["D-1"].price == Decimal("0.10") and rows["D-2"].price == Decimal("19999999.99") and isinstance(rows["D-1"].price, Decimal)
