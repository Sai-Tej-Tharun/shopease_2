"""Profile APIs, authentication and account management. MANDATORY n = scenario n of the task."""
import base64
import json
import time

import httpx
import jwt
import pytest
from sqlalchemy.exc import IntegrityError

from app.core import security
from app.database.connection import SessionLocal
from app.models.user_model import UserProfile
from tests.conftest import PROFILE, SECRET, db_addresses, db_profiles, headers, make_token

URL = "/api/users/profile"


# ====================================================== authentication (scenarios 1, 2)
@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_mandatory_1_profile_api_without_token(client, method):
    r = getattr(client, method)(URL)
    assert r.status_code == 401
    assert r.json()["message"] == "Not authenticated: Bearer token is missing"
    assert r.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("bad", ["abc.def.ghi", "not-a-jwt", "a.b"])
def test_mandatory_2_profile_api_with_invalid_token(client, bad):
    r = client.get(URL, headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401 and r.json()["message"] == "Invalid token"


def test_expired_token_rejected(client):
    r = client.get(URL, headers=headers(1, expires_in=-10))
    assert r.status_code == 401 and r.json()["message"] == "Token has expired"


def test_token_signed_with_another_secret_rejected(client):
    fake = jwt.encode({"sub": "1", "exp": time.time() + 600}, "another-secret-another-secret-another", algorithm="HS256")
    assert client.get(URL, headers={"Authorization": f"Bearer {fake}"}).status_code == 401


def test_alg_none_and_tampered_tokens_rejected(client):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    none_token = f"{enc({'alg': 'none', 'typ': 'JWT'})}.{enc({'sub': '1', 'exp': 9999999999})}."
    assert client.get(URL, headers={"Authorization": f"Bearer {none_token}"}).status_code == 401
    h, _, sig = make_token(1).split(".")
    forged = f"{h}.{enc({'sub': '2', 'role': 'admin', 'exp': 9999999999})}.{sig}"
    assert client.get(URL, headers={"Authorization": f"Bearer {forged}"}).status_code == 401


@pytest.mark.parametrize("claims", [{"exp": 9999999999}, {"sub": "abc", "exp": 9999999999}, {"sub": "0", "exp": 9999999999},
                                    {"sub": "-4", "exp": 9999999999}, {"sub": "1"}])
def test_tokens_with_bad_claims_rejected(client, claims):
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    assert client.get(URL, headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ====================================================== create (scenarios 3, 4)
def test_mandatory_3_create_profile(client):
    r = client.post(URL, json=PROFILE, headers=headers(1))
    assert r.status_code == 201
    body = r.json()
    assert body["auth_user_id"] == 1 and body["full_name"] == "Sai Tej" and body["phone_number"] == "9876543210"
    assert body["date_of_birth"] == "2000-05-17" and body["gender"] == "Male" and body["is_active"] is True
    assert {"id", "created_at", "updated_at", "profile_image"} <= set(body)
    rows = db_profiles()  # database check
    assert len(rows) == 1 and rows[0].auth_user_id == 1 and rows[0].full_name == "Sai Tej" and rows[0].is_active


def test_owner_comes_from_the_token_not_from_the_body(client):
    r = client.post(URL, json={**PROFILE, "auth_user_id": 99}, headers=headers(5))
    assert r.status_code == 422
    assert r.json()["details"] == [{"field": "auth_user_id", "message": "Field is not allowed"}]
    assert db_profiles() == []
    assert client.post(URL, json=PROFILE, headers=headers(5)).json()["auth_user_id"] == 5


def test_mandatory_4_duplicate_profile(client, profile):
    r = client.post(URL, json={**PROFILE, "full_name": "Someone Else"}, headers=headers(1))
    assert r.status_code == 409 and r.json()["message"] == "A profile already exists for this user"
    assert len(db_profiles()) == 1 and db_profiles()[0].full_name == "Sai Tej"


def test_unique_constraint_exists_in_the_database(profile):
    with SessionLocal() as db:
        db.add(UserProfile(auth_user_id=1, full_name="Dup", phone_number="9876543210"))
        with pytest.raises(IntegrityError):
            db.commit()


def test_minimal_profile_only_required_fields(client):
    r = client.post(URL, json={"full_name": "Asha", "phone_number": "+91 98765-43210"}, headers=headers(3))
    assert r.status_code == 201
    assert r.json()["phone_number"] == "+919876543210" and r.json()["date_of_birth"] is None and r.json()["gender"] is None


# ====================================================== read (scenario 5)
def test_mandatory_5_get_own_profile(client, profile):
    r = client.get(URL, headers=headers(1))
    assert r.status_code == 200 and r.json() == profile


def test_get_profile_before_creating_one(client):
    r = client.get(URL, headers=headers(7))
    assert r.status_code == 404 and "Create your profile first" in r.json()["message"]


# ====================================================== update / patch (scenarios 6, 7)
def test_mandatory_6_update_profile(client, profile):
    new = {"full_name": "Sai Tej Tharun", "phone_number": "9000011111", "date_of_birth": "1999-01-02", "gender": "female",
           "profile_image": "/images/me.png"}
    r = client.put(URL, json=new, headers=headers(1))
    assert r.status_code == 200
    assert r.json()["full_name"] == "Sai Tej Tharun" and r.json()["gender"] == "Female" and r.json()["id"] == profile["id"]
    row = db_profiles()[0]
    assert (row.full_name, row.phone_number, str(row.date_of_birth), row.gender, row.profile_image) == \
           ("Sai Tej Tharun", "9000011111", "1999-01-02", "Female", "/images/me.png")


def test_put_clears_optional_fields_that_are_left_out(client, profile):
    r = client.put(URL, json={"full_name": "Sai Tej", "phone_number": "9876543210"}, headers=headers(1))
    assert r.status_code == 200 and r.json()["date_of_birth"] is None and r.json()["profile_image"] is None
    assert db_profiles()[0].gender is None


def test_put_requires_required_fields(client, profile):
    assert client.put(URL, json={"full_name": "Only Name"}, headers=headers(1)).status_code == 422


def test_mandatory_7_partial_update_profile(client, profile):
    r = client.patch(URL, json={"phone_number": "9111122222"}, headers=headers(1))
    assert r.status_code == 200
    assert r.json()["phone_number"] == "9111122222"
    assert r.json()["full_name"] == "Sai Tej" and r.json()["date_of_birth"] == "2000-05-17"  # untouched
    row = db_profiles()[0]
    assert row.phone_number == "9111122222" and row.full_name == "Sai Tej" and row.gender == "Male"


def test_patch_can_clear_an_optional_field(client, profile):
    r = client.patch(URL, json={"profile_image": None}, headers=headers(1))
    assert r.status_code == 200 and r.json()["profile_image"] is None and r.json()["gender"] == "Male"


def test_patch_rejects_empty_body_and_null_required_fields(client, profile):
    assert client.patch(URL, json={}, headers=headers(1)).status_code == 422
    assert client.patch(URL, json={"full_name": None}, headers=headers(1)).status_code == 422
    assert client.patch(URL, json={"auth_user_id": 2}, headers=headers(1)).status_code == 422


def test_update_requires_an_existing_profile(client):
    assert client.put(URL, json=PROFILE, headers=headers(8)).status_code == 404
    assert client.patch(URL, json={"gender": "Male"}, headers=headers(8)).status_code == 404
    assert client.delete(URL, headers=headers(8)).status_code == 404


# ====================================================== validation
@pytest.mark.parametrize("phone", ["12345", "abcdefghij", "98765 4321", "+", "9876543210123456", "", "98765@43210"])
def test_invalid_phone_numbers(client, phone):
    r = client.post(URL, json={**PROFILE, "phone_number": phone}, headers=headers(1))
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "phone_number"


@pytest.mark.parametrize("phone,stored", [("9876543210", "9876543210"), ("+91 98765 43210", "+919876543210"),
                                          ("(987) 654-3210", "9876543210"), ("+1-415-555-0100", "+14155550100")])
def test_valid_phone_formats_are_normalised(client, phone, stored):
    r = client.post(URL, json={**PROFILE, "phone_number": phone}, headers=headers(1))
    assert r.status_code == 201 and r.json()["phone_number"] == stored


@pytest.mark.parametrize("name", ["", "A", "1234", "<script>", "x" * 101, "   "])
def test_invalid_full_name(client, name):
    r = client.post(URL, json={**PROFILE, "full_name": name}, headers=headers(1))
    assert r.status_code == 422 and r.json()["details"][0]["field"] == "full_name"


@pytest.mark.parametrize("field,value", [("date_of_birth", "2999-01-01"), ("date_of_birth", "1800-01-01"),
                                         ("date_of_birth", "not-a-date"), ("gender", "robot"),
                                         ("profile_image", "javascript:alert(1)"), ("profile_image", "has space.png"),
                                         ("profile_image", "/" + "a" * 260)])
def test_invalid_optional_fields(client, field, value):
    r = client.post(URL, json={**PROFILE, field: value}, headers=headers(1))
    assert r.status_code == 422 and r.json()["details"][0]["field"] == field


def test_missing_required_fields(client):
    r = client.post(URL, json={}, headers=headers(1))
    assert r.status_code == 422
    assert {d["field"] for d in r.json()["details"]} == {"full_name", "phone_number"}
    assert all(d["message"] == "Field is required" for d in r.json()["details"])


def test_validation_errors_do_not_echo_submitted_values(client):
    r = client.post(URL, json={**PROFILE, "phone_number": "SECRET-VALUE-123"}, headers=headers(1))
    assert "SECRET-VALUE-123" not in r.text


# ====================================================== other users' profiles (scenario 14)
def test_mandatory_14_user_cannot_see_or_change_another_users_profile(client, profile):
    # user 2 has no profile: the API never returns user 1's data to them
    assert client.get(URL, headers=headers(2)).status_code == 404
    # user 2 tries to change user 1's profile by naming the id in the body -> field not allowed, nothing changes
    r = client.put(URL, json={**PROFILE, "auth_user_id": 1, "full_name": "Hacked Name"}, headers=headers(2))
    assert r.status_code in (404, 422)
    r = client.patch(URL, json={"auth_user_id": 1, "full_name": "Hacked Name"}, headers=headers(2))
    assert r.status_code in (404, 422)
    assert db_profiles()[0].full_name == "Sai Tej"
    # once user 2 has their own profile, every call touches only their own row
    client.post(URL, json={**PROFILE, "full_name": "Second User", "phone_number": "9123456780"}, headers=headers(2))
    client.patch(URL, json={"full_name": "Second Changed"}, headers=headers(2))
    assert client.get(URL, headers=headers(1)).json()["full_name"] == "Sai Tej"
    assert client.get(URL, headers=headers(2)).json()["full_name"] == "Second Changed"
    client.delete(URL, headers=headers(2))
    assert [p.auth_user_id for p in db_profiles()] == [1]


# ====================================================== delete profile
def test_delete_profile_removes_its_addresses_only(client, profile, profile2):
    from tests.conftest import HOME
    client.post("/api/users/addresses", json=HOME, headers=headers(1))
    client.post("/api/users/addresses", json=HOME, headers=headers(2))
    r = client.delete(URL, headers=headers(1))
    assert r.status_code == 200 and r.json()["message"] == "Profile and addresses deleted successfully"
    assert [p.auth_user_id for p in db_profiles()] == [2]
    assert [a.user_id for a in db_addresses()] == [profile2["id"]]
    assert client.get(URL, headers=headers(1)).status_code == 404


# ====================================================== account management / deactivation
def test_account_overview(client, profile):
    from tests.conftest import HOME, OFFICE
    r = client.get("/api/users/account", headers=headers(1, role="customer"))
    assert r.status_code == 200 and r.json()["profile_status"] == "active" and r.json()["address_count"] == 0
    client.post("/api/users/addresses", json=HOME, headers=headers(1))
    client.post("/api/users/addresses", json=OFFICE, headers=headers(1))
    body = client.get("/api/users/account", headers=headers(1)).json()
    assert body["auth_user_id"] == 1 and body["role"] == "customer" and body["has_profile"] is True
    assert body["address_count"] == 2 and body["default_address"]["address_type"] == "Home"
    assert body["profile"]["full_name"] == "Sai Tej"


def test_account_overview_without_profile_and_without_token(client):
    r = client.get("/api/users/account", headers=headers(9))
    assert r.status_code == 200 and r.json()["has_profile"] is False and r.json()["profile_status"] == "none"
    assert client.get("/api/users/account").status_code == 401


def test_deactivate_and_reactivate_profile(client, profile):
    from tests.conftest import HOME
    client.post("/api/users/addresses", json=HOME, headers=headers(1))
    r = client.post(f"{URL}/deactivate", headers=headers(1))
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert db_profiles()[0].is_active is False and len(db_addresses()) == 1  # nothing is deleted
    # read-only view still works; changes and address operations are blocked with 403
    assert client.get(URL, headers=headers(1)).status_code == 200
    assert client.get("/api/users/account", headers=headers(1)).json()["profile_status"] == "deactivated"
    for call in (lambda: client.put(URL, json=PROFILE, headers=headers(1)),
                 lambda: client.patch(URL, json={"gender": "Male"}, headers=headers(1)),
                 lambda: client.get("/api/users/addresses", headers=headers(1)),
                 lambda: client.post("/api/users/addresses", json=HOME, headers=headers(1))):
        r = call()
        assert r.status_code == 403 and "deactivated" in r.json()["message"]
    assert client.post(f"{URL}/deactivate", headers=headers(1)).status_code == 409
    r = client.post(f"{URL}/reactivate", headers=headers(1))
    assert r.status_code == 200 and r.json()["is_active"] is True
    assert client.get("/api/users/addresses", headers=headers(1)).status_code == 200
    assert client.post(f"{URL}/reactivate", headers=headers(1)).status_code == 409


def test_deactivation_does_not_call_the_authentication_service(client, profile, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the User Service must not call the Authentication Service to deactivate a profile")
    monkeypatch.setattr(security.httpx, "post", boom)
    assert client.post(f"{URL}/deactivate", headers=headers(1)).status_code == 200


def test_deactivating_one_profile_does_not_affect_another_user(client, profile, profile2):
    client.post(f"{URL}/deactivate", headers=headers(1))
    assert client.get("/api/users/addresses", headers=headers(2)).status_code == 200


# ====================================================== optional remote token verification
class FakeResponse:
    def __init__(self, status, body=None):
        self.status_code, self._body = status, body or {}

    def json(self):
        return self._body


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setattr(security.settings, "auth_verify_remote", True)
    calls = []

    def fake_post(url, headers, timeout):
        calls.append((url, headers["Authorization"]))
        return fake_post.response

    monkeypatch.setattr(security.httpx, "post", fake_post)
    return fake_post, calls


def test_remote_verification_success_sends_the_token_to_the_auth_service(client, remote):
    fake_post, calls = remote
    fake_post.response = FakeResponse(200, {"valid": True})
    r = client.post(URL, json=PROFILE, headers=headers(1))
    assert r.status_code == 201
    assert calls[0][0] == "http://localhost:8001/api/auth/verify-token" and calls[0][1].startswith("Bearer ")


@pytest.mark.parametrize("status,expected", [(401, 401), (403, 403), (500, 503)])
def test_remote_verification_rejections(client, remote, status, expected):
    fake_post, _ = remote
    fake_post.response = FakeResponse(status, {"message": "Account is inactive"})
    r = client.get(URL, headers=headers(1))
    assert r.status_code == expected


def test_remote_verification_when_auth_service_is_down(client, remote, monkeypatch):
    def down(*a, **k):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(security.httpx, "post", down)
    r = client.get(URL, headers=headers(1))
    assert r.status_code == 503 and "Authentication Service" in r.json()["message"]


def test_remote_verification_is_skipped_for_invalid_tokens(client, remote):
    fake_post, calls = remote
    fake_post.response = FakeResponse(200)
    assert client.get(URL, headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    assert calls == []  # an invalid token never leaves the service


# ====================================================== database errors, general
def test_database_error_returns_500_rolls_back_and_service_keeps_working(client, profile, monkeypatch):
    from sqlalchemy.exc import OperationalError
    from app.services import user_service
    real_commit = user_service.commit
    state = {"fail": True}

    def flaky(db):
        if state["fail"]:
            state["fail"] = False
            db.rollback()
            raise OperationalError("UPDATE", {}, Exception("secret internal detail"))
        return real_commit(db)

    monkeypatch.setattr(user_service, "commit", flaky)
    r = client.patch(URL, json={"full_name": "Changed Name"}, headers=headers(1))
    assert r.status_code == 500 and "secret" not in r.text and r.json()["error"] == "Internal Server Error"
    assert db_profiles()[0].full_name == "Sai Tej"  # nothing was saved
    assert client.patch(URL, json={"full_name": "Changed Name"}, headers=headers(1)).status_code == 200  # recovered


def test_unexpected_error_returns_500_without_details(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import user_service

    def boom(*a, **k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(user_service, "get_profile_by_auth_id", boom)
    r = TestClient(app, raise_server_exceptions=False).get(URL, headers=headers(1))
    assert r.status_code == 500 and "secret" not in r.text


def test_no_password_or_credentials_are_stored(profile):
    from app.database.connection import Base
    columns = {c.name for t in Base.metadata.tables.values() for c in t.columns}
    assert not any(word in name for name in columns for word in ("password", "hash", "token", "secret", "email"))


def test_health_docs_and_routes(client):
    assert client.get("/health").status_code == 200 and client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) >= {"/api/users/profile", "/api/users/profile/deactivate", "/api/users/profile/reactivate",
                          "/api/users/account", "/api/users/addresses", "/api/users/addresses/{address_id}",
                          "/api/users/addresses/{address_id}/default"}
    assert not any("login" in p or "register" in p for p in paths)  # no duplicate login/registration
    assert client.get("/nope").status_code == 404 and client.put("/health").status_code == 405
