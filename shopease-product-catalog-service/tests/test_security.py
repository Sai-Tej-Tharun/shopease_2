"""JWT and role rules. MANDATORY n = scenario n."""
import time

import jwt
import pytest

from tests.conftest import ADMIN, CUSTOMER, SECRET, add_product, headers, make_token, product_body

ADMIN_ONLY = [
    ("post", "/api/products/", lambda: {"json": product_body()}),
    ("put", "/api/products/1", lambda: {"json": product_body()}),
    ("patch", "/api/products/1", lambda: {"json": {"name": "New Name"}}),
    ("delete", "/api/products/1", lambda: {}),
    ("post", "/api/categories/", lambda: {"json": {"name": "Books"}}),
    ("put", "/api/categories/1", lambda: {"json": {"name": "Books"}}),
    ("delete", "/api/categories/1", lambda: {}),
    ("get", "/api/inventory/1", lambda: {}),
    ("post", "/api/inventory/1/stock-in", lambda: {"json": {"quantity": 1}}),
    ("post", "/api/inventory/1/stock-out", lambda: {"json": {"quantity": 1}}),
    ("get", "/api/inventory/1/history", lambda: {}),
    ("get", "/api/inventory/low-stock", lambda: {}),
]
CUSTOMER_READABLE = ["/api/products/", "/api/products/1", "/api/categories/", "/api/categories/1"]


@pytest.mark.parametrize("method,url,kw", ADMIN_ONLY)
def test_mandatory_17_customer_cannot_use_admin_apis(client, product, method, url, kw):
    r = getattr(client, method)(url, headers=CUSTOMER, **kw())
    assert r.status_code == 403 and r.json()["error"] == "Forbidden" and "Administrator" in r.json()["message"]


@pytest.mark.parametrize("method,url,kw", ADMIN_ONLY)
def test_mandatory_18_no_token_gives_401(client, product, method, url, kw):
    r = getattr(client, method)(url, **kw())
    assert r.status_code == 401 and r.json()["message"] == "Not authenticated: Bearer token is missing"


@pytest.mark.parametrize("url", CUSTOMER_READABLE)
def test_read_apis_need_a_token_too(client, product, url):
    assert client.get(url).status_code == 401
    assert client.get(url, headers=CUSTOMER).status_code == 200
    assert client.get(url, headers=ADMIN).status_code == 200


def bad_tokens():
    now = int(time.time())
    yield "garbage", "Invalid token"
    yield "a.b.c", "Invalid token"
    yield make_token(expires_in=-60), "Token has expired"
    yield jwt.encode({"sub": "1", "role": "admin", "exp": now + 600}, "another-secret-another-secret-123456", algorithm="HS256"), "Invalid token"
    yield jwt.encode({"sub": "1", "role": "admin", "exp": now + 600}, SECRET, algorithm="HS512"), "Invalid token"
    yield jwt.encode({"sub": "1", "role": "admin", "exp": now + 600}, None, algorithm="none"), "Invalid token"
    yield jwt.encode({"sub": "1", "role": "admin"}, SECRET, algorithm="HS256"), "Invalid token"  # no exp
    yield jwt.encode({"role": "admin", "exp": now + 600}, SECRET, algorithm="HS256"), "Invalid token"  # no sub
    yield jwt.encode({"sub": "abc", "role": "admin", "exp": now + 600}, SECRET, algorithm="HS256"), "Invalid token"
    yield make_token()[:-4] + "AAAA", "Invalid token"  # tampered signature


@pytest.mark.parametrize("token,message", list(bad_tokens()))
def test_mandatory_19_invalid_tokens_give_401(client, product, token, message):
    for url in ("/api/products/", "/api/inventory/1"):
        r = client.get(url, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["message"] == message


def test_wrong_scheme_and_empty_bearer(client):
    assert client.get("/api/products/", headers={"Authorization": f"Basic {make_token()}"}).status_code == 401
    assert client.get("/api/products/", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get("/api/products/", headers={"Authorization": make_token()}).status_code == 401


def test_role_is_taken_from_the_token(client, product):
    assert client.get("/api/inventory/1", headers=headers(5, "admin")).status_code == 200
    assert client.get("/api/inventory/1", headers=headers(5, " Admin ")).status_code == 200  # role comparison ignores case and spaces
    assert client.get("/api/inventory/1", headers=headers(5, None)).status_code == 403  # no role claim
    assert client.get("/api/inventory/1", headers=headers(5, "superuser")).status_code == 403
    assert client.get("/api/products/", headers=headers(5, None)).status_code == 200  # any valid user may browse


def test_authentication_is_checked_before_validation_and_lookup(client):
    assert client.post("/api/products/", json={}).status_code == 401  # invalid body but no token
    assert client.post("/api/products/", json={}, headers=CUSTOMER).status_code == 403  # customer never reaches validation
    assert client.delete("/api/products/999", headers=CUSTOMER).status_code == 403


def test_no_login_or_registration_endpoints_here(client):
    for url in ("/api/auth/login", "/api/auth/register", "/login", "/register"):
        assert client.post(url, json={"email": "a@b.co", "password": "x"}).status_code == 404


def test_health_is_public_and_errors_are_uniform(client):
    assert client.get("/health").status_code == 200
    r = client.get("/api/nothing", headers=ADMIN)
    assert r.status_code == 404 and set(r.json()) >= {"status_code", "error", "message"}
    assert client.patch("/api/inventory/1", headers=ADMIN).status_code == 405


def test_remote_verification_with_authentication_service(client, product, monkeypatch):
    from app.core import security
    from app.core.config import settings
    monkeypatch.setattr(settings, "auth_verify_remote", True)
    seen = {}

    class Resp:
        def __init__(self, code): self.status_code = code

        def json(self):
            raise ValueError("no body")

    def fake_post(url, headers=None, timeout=None):
        seen["url"], seen["auth"] = url, headers["Authorization"]
        return Resp(200)

    monkeypatch.setattr(security.httpx, "post", fake_post)
    assert client.get("/api/products/", headers=CUSTOMER).status_code == 200
    assert seen["url"].endswith("/api/auth/verify-token") and seen["auth"].startswith("Bearer ")
    monkeypatch.setattr(security.httpx, "post", lambda *a, **k: Resp(401))
    r = client.get("/api/products/", headers=CUSTOMER)
    assert r.status_code == 401
    monkeypatch.setattr(security.httpx, "post", lambda *a, **k: Resp(403))
    assert client.get("/api/products/", headers=CUSTOMER).status_code == 403

    def unreachable(*a, **k):
        raise security.httpx.ConnectError("down")

    monkeypatch.setattr(security.httpx, "post", unreachable)
    assert client.get("/api/products/", headers=CUSTOMER).status_code == 503


def test_unexpected_error_hides_details(client, product, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import category_service
    monkeypatch.setattr(category_service, "list_categories", lambda *a, **k: 1 / 0)
    r = TestClient(app, raise_server_exceptions=False).get("/api/categories/", headers=ADMIN)
    assert r.status_code == 500 and "division" not in r.text.lower() and "Traceback" not in r.text
