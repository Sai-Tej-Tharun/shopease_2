"""Category APIs. MANDATORY n = scenario n of the task."""
import pytest

from tests.conftest import ADMIN, CATEGORY, CUSTOMER, add_product, db_categories

URL = "/api/categories/"


def test_create_category(client):
    r = client.post(URL, json=CATEGORY, headers=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Electronics" and body["is_active"] is True and body["id"] == 1
    assert {"created_at", "updated_at", "description"} <= set(body)
    assert [(c.name, c.is_active) for c in db_categories()] == [("Electronics", True)]


def test_mandatory_9_duplicate_category_name(client, category):
    for name in ("Electronics", "electronics", "  ELECTRONICS "):
        r = client.post(URL, json={"name": name}, headers=ADMIN)
        assert r.status_code == 409 and "already exists" in r.json()["message"]
    assert len(db_categories()) == 1


@pytest.mark.parametrize("name", ["", "A", "   ", "<script>", "x" * 101, "!!!"])
def test_invalid_category_names(client, name):
    r = client.post(URL, json={"name": name}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "name"


def test_category_validation_details(client):
    r = client.post(URL, json={}, headers=ADMIN)
    assert r.json()["details"] == [{"field": "name", "message": "Field is required"}]
    r = client.post(URL, json={"name": "Books", "unknown": 1}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["details"][0]["message"] == "Field is not allowed"
    assert client.post(URL, json={"name": "Books", "description": "x" * 2001}, headers=ADMIN).status_code == 422


def test_list_and_get_categories(client, category):
    client.post(URL, json={"name": "Books"}, headers=ADMIN)
    assert [c["name"] for c in client.get(URL, headers=ADMIN).json()] == ["Books", "Electronics"]
    assert client.get(f"{URL}1", headers=CUSTOMER).json()["name"] == "Electronics"
    assert client.get(f"{URL}999", headers=ADMIN).status_code == 404
    assert client.get(f"{URL}abc", headers=ADMIN).status_code == 422


def test_update_category_and_activate_deactivate(client, category):
    r = client.put(f"{URL}1", json={"name": "Electronics & Gadgets", "description": "New", "is_active": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["name"] == "Electronics & Gadgets" and r.json()["is_active"] is False
    assert db_categories()[0].is_active is False
    assert client.put(f"{URL}1", json={"name": "Electronics & Gadgets", "is_active": True}, headers=ADMIN).json()["is_active"] is True
    assert client.put(f"{URL}1", json={"name": "Same"}, headers=ADMIN).json()["description"] is None  # PUT replaces
    assert client.put(f"{URL}999", json={"name": "Nope"}, headers=ADMIN).status_code == 404


def test_update_to_an_existing_name_is_a_conflict_but_own_name_is_fine(client, category):
    client.post(URL, json={"name": "Books"}, headers=ADMIN)
    assert client.put(f"{URL}2", json={"name": "electronics"}, headers=ADMIN).status_code == 409
    assert client.put(f"{URL}1", json={"name": "ELECTRONICS"}, headers=ADMIN).status_code == 200


def test_customers_only_see_active_categories(client, category):
    client.post(URL, json={"name": "Hidden", "is_active": False}, headers=ADMIN)
    assert [c["name"] for c in client.get(URL, headers=CUSTOMER).json()] == ["Electronics"]
    assert client.get(f"{URL}2", headers=CUSTOMER).status_code == 404
    assert client.get(f"{URL}2", headers=ADMIN).status_code == 200
    assert [c["name"] for c in client.get(URL, headers=ADMIN).json()] == ["Electronics", "Hidden"]
    assert [c["name"] for c in client.get(URL + "?is_active=false", headers=ADMIN).json()] == ["Hidden"]
    assert len(client.get(URL + "?is_active=false", headers=CUSTOMER).json()) == 1  # customers cannot see inactive ones


def test_delete_empty_category(client, category):
    r = client.delete(f"{URL}1", headers=ADMIN)
    assert r.status_code == 200 and r.json()["message"] == "Category deleted successfully"
    assert db_categories() == [] and client.delete(f"{URL}1", headers=ADMIN).status_code == 404


def test_cannot_delete_category_that_still_has_products(client, product):
    r = client.delete(f"{URL}1", headers=ADMIN)
    assert r.status_code == 409 and "still contains 1 product" in r.json()["message"]
    assert len(db_categories()) == 1
    client.delete(f"/api/products/{product['id']}", headers=ADMIN)  # rule: handle the products first
    assert client.delete(f"{URL}1", headers=ADMIN).status_code == 200


def test_cannot_delete_category_with_only_inactive_products(client, product):
    client.patch(f"/api/products/{product['id']}", json={"is_active": False}, headers=ADMIN)
    assert client.delete(f"{URL}1", headers=ADMIN).status_code == 409


def test_products_can_be_moved_then_category_deleted(client, product):
    client.post(URL, json={"name": "Phones"}, headers=ADMIN)
    assert client.patch(f"/api/products/{product['id']}", json={"category_id": 2}, headers=ADMIN).status_code == 200
    assert client.delete(f"{URL}1", headers=ADMIN).status_code == 200


def test_deactivating_a_category_hides_its_products_from_customers(client, product):
    client.put(f"{URL}1", json={"name": "Electronics", "is_active": False}, headers=ADMIN)
    assert client.get("/api/products/", headers=CUSTOMER).json()["total"] == 0
    assert client.get(f"/api/products/{product['id']}", headers=CUSTOMER).status_code == 404
    assert client.get(f"/api/products/{product['id']}", headers=ADMIN).status_code == 200
    assert client.get("/api/products/", headers=ADMIN).json()["total"] == 1


def test_database_enforces_unique_names_and_foreign_key(client, category):
    from sqlalchemy.exc import IntegrityError
    from app.database.connection import SessionLocal
    from app.models.category_model import Category
    from app.models.product_model import Product
    with SessionLocal() as db:
        db.add(Category(name="Electronics"))
        with pytest.raises(IntegrityError):
            db.commit()
    with SessionLocal() as db:  # RESTRICT: the database also refuses to delete a category that has products
        add_product(client)
        db.delete(db.get(Category, 1))
        with pytest.raises(IntegrityError):
            db.commit()
