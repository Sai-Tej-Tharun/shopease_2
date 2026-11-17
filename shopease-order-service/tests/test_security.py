"""JWT and role rules for the Order Service."""
import time

import jwt
import pytest

from tests.conftest import ADMIN, ALICE, BOB, SECRET, body, headers, make_token, place

URL = "/api/orders/"
ADMIN_ONLY = [("patch", f"{URL}1/status", {"json": {"status": "CONFIRMED"}}), ("get", f"{URL}compensations", {}), ("post", f"{URL}compensations/retry", {})]
ALL_ENDPOINTS = [("post", URL, {"json": body()}), ("get", URL, {}), ("get", f"{URL}1", {}), ("get", f"{URL}number/ORD-X", {}), ("get", f"{URL}1/items", {}),
                 ("get", f"{URL}1/items/1", {}), ("post", f"{URL}1/cancel", {})] + ADMIN_ONLY


@pytest.mark.parametrize("method,url,kw", ALL_ENDPOINTS)
def test_no_token_gives_401_everywhere(client, method, url, kw):
    r = getattr(client, method)(url, **kw)
    assert r.status_code == 401 and r.json()["message"] == "Not authenticated: Bearer token is missing"


def bad_tokens():
    now = int(time.time())
    yield "garbage", "Invalid token"
    yield make_token(expires_in=-60), "Token has expired"
    yield jwt.encode({"sub": "11", "role": "admin", "exp": now + 600}, "another-secret-another-secret-123456", algorithm="HS256"), "Invalid token"
    yield jwt.encode({"sub": "11", "role": "admin", "exp": now + 600}, None, algorithm="none"), "Invalid token"
    yield jwt.encode({"role": "admin", "exp": now + 600}, SECRET, algorithm="HS256"), "Invalid token"
    yield jwt.encode({"sub": "abc", "exp": now + 600}, SECRET, algorithm="HS256"), "Invalid token"
    yield make_token()[:-4] + "AAAA", "Invalid token"


@pytest.mark.parametrize("token,message", list(bad_tokens()))
def test_invalid_tokens_give_401(client, token, message):
    for call in (lambda h: client.get(URL, headers=h), lambda h: client.post(URL, json=body(), headers=h)):
        r = call({"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["message"] == message


@pytest.mark.parametrize("method,url,kw", ADMIN_ONLY)
def test_customer_gets_403_on_admin_endpoints(client, catalog, method, url, kw):
    r = getattr(client, method)(url, headers=ALICE, **kw)
    assert r.status_code == 403 and r.json()["error"] == "Forbidden"


def test_roles_come_from_the_token(client, catalog):
    o = place(client)
    assert client.patch(f"{URL}{o['id']}/status", json={"status": "CONFIRMED"}, headers=headers(5, " Admin ")).status_code == 200
    for role in (None, "superuser", "customer", ""):
        assert client.patch(f"{URL}{o['id']}/status", json={"status": "PROCESSING"}, headers=headers(5, role)).status_code == 403


def test_service_token_is_short_lived_and_not_the_customers_token(client, catalog, monkeypatch):
    seen = []
    from app.services import product_client
    original = product_client.create_service_token
    monkeypatch.setattr(product_client, "create_service_token", lambda: seen.append(original()) or seen[-1])
    place(client)
    claims = jwt.decode(seen[0], SECRET, algorithms=["HS256"])
    assert claims["sub"] == "999999" and claims["role"] == "admin" and claims["exp"] - claims["iat"] == 60
    assert seen[0] != ALICE["Authorization"].split()[1]


def test_customer_token_is_forwarded_to_the_user_service_only(client, catalog, users, monkeypatch):
    seen = {}
    original = users.__call__

    def spy(request):
        seen[request.url.path] = request.headers["authorization"]
        return original(request)

    import httpx
    from app.services import user_client
    monkeypatch.setattr(user_client, "TRANSPORT", httpx.MockTransport(spy))
    place(client)
    token = ALICE["Authorization"]
    assert seen["/api/users/profile"] == token and seen["/api/users/addresses/1"] == token


def test_no_login_or_registration_endpoints(client):
    for url in ("/api/auth/login", "/api/auth/register", "/login", "/register"):
        assert client.post(url, json={"email": "a@b.co", "password": "x"}).status_code == 404


def test_health_and_error_shape(client):
    assert client.get("/health").status_code == 200
    r = client.get("/api/nothing", headers=ADMIN)
    assert r.status_code == 404 and set(r.json()) >= {"status_code", "error", "message"}
    assert client.put(f"{URL}1", headers=ADMIN).status_code == 405


def test_remote_token_verification(client, catalog, monkeypatch):
    from app.core import security
    from app.core.config import settings
    monkeypatch.setattr(settings, "auth_verify_remote", True)

    class Resp:
        def __init__(self, code): self.status_code = code
        def json(self): raise ValueError()

    monkeypatch.setattr(security.httpx, "post", lambda *a, **k: Resp(200))
    assert client.get(URL, headers=ALICE).status_code == 200
    monkeypatch.setattr(security.httpx, "post", lambda *a, **k: Resp(401))
    assert client.get(URL, headers=ALICE).status_code == 401

    def down(*a, **k):
        raise security.httpx.ConnectError("down")

    monkeypatch.setattr(security.httpx, "post", down)
    assert client.get(URL, headers=ALICE).status_code == 503


def test_unexpected_error_hides_details(client, catalog, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import order_service
    monkeypatch.setattr(order_service, "list_orders", lambda *a, **k: 1 / 0)
    r = TestClient(app, raise_server_exceptions=False).get(URL, headers=ALICE)
    assert r.status_code == 500 and "division" not in r.text.lower() and "Traceback" not in r.text
