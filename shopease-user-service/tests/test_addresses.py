"""Address APIs. MANDATORY n = scenario n of the task."""
import pytest

from tests.conftest import HOME, OFFICE, OTHER, db_addresses, headers

URL = "/api/users/addresses"


def add(client, body, user=1):
    r = client.post(URL, json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def defaults(user_id):
    return [a.id for a in db_addresses(user_id) if a.is_default]


# ====================================================== add / list (scenarios 8, 9)
def test_mandatory_8_add_multiple_addresses(client, profile):
    a, b, c = add(client, HOME), add(client, OFFICE), add(client, OTHER)
    assert (a["id"], b["id"], c["id"]) == (1, 2, 3)
    assert a["user_id"] == profile["id"] and a["address_type"] == "Home" and a["postal_code"] == "500001"
    assert a["is_default"] is True and b["is_default"] is False and c["is_default"] is False
    rows = db_addresses()  # database check
    assert [r.address_type for r in rows] == ["Home", "Office", "Other"]
    assert [r.is_default for r in rows] == [True, False, False] and all(r.user_id == profile["id"] for r in rows)
    assert rows[1].address_line_2 is None


def test_first_address_is_default_even_if_not_requested(client, profile):
    assert add(client, {**HOME, "is_default": False})["is_default"] is True


def test_mandatory_9_list_all_addresses(client, profile):
    add(client, HOME), add(client, OFFICE), add(client, OTHER)
    r = client.get(URL, headers=headers(1))
    assert r.status_code == 200 and [a["address_type"] for a in r.json()] == ["Home", "Office", "Other"]
    assert r.json()[0]["is_default"] is True  # default address is listed first


def test_list_is_empty_for_new_profile_and_default_listed_first(client, profile):
    assert client.get(URL, headers=headers(1)).json() == []
    add(client, HOME), add(client, OFFICE)
    client.patch(f"{URL}/2/default", headers=headers(1))
    assert [a["id"] for a in client.get(URL, headers=headers(1)).json()] == [2, 1]


def test_get_single_address(client, profile):
    add(client, HOME)
    r = client.get(f"{URL}/1", headers=headers(1))
    assert r.status_code == 200 and r.json()["address_line_1"] == "12-3 Main Road"


# ====================================================== update (scenario 10)
def test_mandatory_10_update_address(client, profile):
    add(client, HOME), add(client, OFFICE)
    new = {**OFFICE, "address_line_1": "99 New Tower", "city": "Pune", "state": "Maharashtra", "postal_code": "411001",
           "address_line_2": "Floor 4"}
    r = client.put(f"{URL}/2", json=new, headers=headers(1))
    assert r.status_code == 200
    assert r.json()["address_line_1"] == "99 New Tower" and r.json()["city"] == "Pune" and r.json()["id"] == 2
    row = db_addresses()[1]
    assert (row.address_line_1, row.city, row.state, row.postal_code, row.address_line_2) == \
           ("99 New Tower", "Pune", "Maharashtra", "411001", "Floor 4")
    assert defaults(1) == [1]  # unrelated default untouched


def test_put_with_is_default_true_moves_the_default(client, profile):
    add(client, HOME), add(client, OFFICE)
    client.put(f"{URL}/2", json={**OFFICE, "is_default": True}, headers=headers(1))
    assert defaults(1) == [2]


def test_put_with_is_default_false_never_removes_the_only_default(client, profile):
    add(client, HOME)
    r = client.put(f"{URL}/1", json={**HOME, "is_default": False}, headers=headers(1))
    assert r.status_code == 200 and r.json()["is_default"] is True and defaults(1) == [1]


# ====================================================== default address (scenarios 11, 12)
def test_mandatory_11_set_default_address(client, profile):
    add(client, HOME), add(client, OFFICE), add(client, OTHER)
    r = client.patch(f"{URL}/3/default", headers=headers(1))
    assert r.status_code == 200 and r.json()["id"] == 3 and r.json()["is_default"] is True
    assert [a.is_default for a in db_addresses()] == [False, False, True]


def test_mandatory_12_only_one_default_exists(client, profile):
    add(client, HOME)  # default
    add(client, {**OFFICE, "is_default": True})  # new default replaces the old one
    assert defaults(1) == [2]
    add(client, {**OTHER, "is_default": True})
    assert defaults(1) == [3]
    for target in (1, 2, 1, 3, 2):
        client.patch(f"{URL}/{target}/default", headers=headers(1))
        assert defaults(1) == [target]
    api = client.get(URL, headers=headers(1)).json()
    assert sum(a["is_default"] for a in api) == 1


def test_setting_default_does_not_touch_other_users(client, profile, profile2):
    add(client, HOME, 1), add(client, HOME, 2), add(client, OFFICE, 2)
    client.patch(f"{URL}/3/default", headers=headers(2))
    assert defaults(profile["id"]) == [1] and defaults(profile2["id"]) == [3]


# ====================================================== delete (scenario 13)
def test_mandatory_13_delete_address(client, profile):
    add(client, HOME), add(client, OFFICE)
    r = client.delete(f"{URL}/2", headers=headers(1))
    assert r.status_code == 200 and r.json()["message"] == "Address deleted successfully"
    assert [a.id for a in db_addresses()] == [1]
    assert client.get(f"{URL}/2", headers=headers(1)).status_code == 404


def test_deleting_the_default_promotes_the_newest_remaining_address(client, profile):
    add(client, HOME), add(client, OFFICE), add(client, OTHER)
    client.delete(f"{URL}/1", headers=headers(1))
    assert defaults(1) == [3]
    client.delete(f"{URL}/3", headers=headers(1))
    assert defaults(1) == [2]
    client.delete(f"{URL}/2", headers=headers(1))
    assert db_addresses() == []


def test_deleting_a_non_default_keeps_the_default(client, profile):
    add(client, HOME), add(client, OFFICE)
    client.delete(f"{URL}/2", headers=headers(1))
    assert defaults(1) == [1]


# ====================================================== other users' addresses (scenario 15)
def test_mandatory_15_cannot_access_another_users_address(client, profile, profile2):
    add(client, HOME, 1)  # address id 1 belongs to user 1
    add(client, OFFICE, 2)  # address id 2 belongs to user 2
    h2 = headers(2)
    for call in (lambda: client.get(f"{URL}/1", headers=h2),
                 lambda: client.put(f"{URL}/1", json={**HOME, "city": "Hacked"}, headers=h2),
                 lambda: client.delete(f"{URL}/1", headers=h2),
                 lambda: client.patch(f"{URL}/1/default", headers=h2)):
        r = call()
        assert r.status_code == 403 and r.json()["message"] == "You do not have permission to access this address"
    row = db_addresses(profile["id"])[0]  # database unchanged
    assert row.city == "Hyderabad" and row.is_default is True and len(db_addresses()) == 2
    # and user 1 still sees only their own
    assert [a["id"] for a in client.get(URL, headers=headers(1)).json()] == [1]
    assert [a["id"] for a in client.get(URL, headers=h2).json()] == [2]


def test_cannot_create_an_address_for_another_user(client, profile, profile2):
    for field in ("user_id", "auth_user_id", "id"):
        r = client.post(URL, json={**HOME, field: 1}, headers=headers(2))
        assert r.status_code == 422 and r.json()["details"][0] == {"field": field, "message": "Field is not allowed"}
    assert db_addresses() == []


def test_address_not_found(client, profile):
    for call in (lambda: client.get(f"{URL}/999", headers=headers(1)),
                 lambda: client.put(f"{URL}/999", json=HOME, headers=headers(1)),
                 lambda: client.delete(f"{URL}/999", headers=headers(1)),
                 lambda: client.patch(f"{URL}/999/default", headers=headers(1))):
        r = call()
        assert r.status_code == 404 and r.json()["message"] == "Address with id 999 not found"


def test_non_numeric_address_id(client, profile):
    assert client.get(f"{URL}/abc", headers=headers(1)).status_code == 422


# ====================================================== authentication and profile requirements
@pytest.mark.parametrize("method,path", [("get", ""), ("post", ""), ("get", "/1"), ("put", "/1"), ("delete", "/1"), ("patch", "/1/default")])
def test_address_apis_require_a_valid_token(client, method, path):
    assert getattr(client, method)(URL + path).status_code == 401
    assert getattr(client, method)(URL + path, headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_address_apis_require_a_profile_first(client):
    r = client.post(URL, json=HOME, headers=headers(4))
    assert r.status_code == 404 and "Create your profile first" in r.json()["message"]
    assert client.get(URL, headers=headers(4)).status_code == 404


# ====================================================== validation
BAD = [("address_type", "Cabin"), ("full_name", "1"), ("phone_number", "123"), ("address_line_1", "ab"),
       ("city", "1234"), ("state", ""), ("country", "<x>"), ("postal_code", "!"), ("postal_code", "123456789012"),
       ("is_default", "maybe"), ("address_line_2", "x" * 300)]


@pytest.mark.parametrize("field,value", BAD)
def test_invalid_address_fields(client, profile, field, value):
    r = client.post(URL, json={**HOME, field: value}, headers=headers(1))
    assert r.status_code == 422 and r.json()["details"][0]["field"] == field
    assert db_addresses() == []


@pytest.mark.parametrize("field", ["address_type", "full_name", "phone_number", "address_line_1", "city", "state",
                                   "country", "postal_code"])
def test_each_mandatory_address_field_is_required(client, profile, field):
    body = {k: v for k, v in HOME.items() if k != field}
    r = client.post(URL, json=body, headers=headers(1))
    assert r.status_code == 422 and r.json()["details"] == [{"field": field, "message": "Field is required"}]


def test_address_normalisation(client, profile):
    a = add(client, {**HOME, "address_type": "office", "postal_code": " sw1a 1aa ", "phone_number": "+91 98765-43210",
                     "city": "  new   delhi ", "address_line_2": "   "})
    assert (a["address_type"], a["postal_code"], a["phone_number"], a["city"], a["address_line_2"]) == \
           ("Office", "SW1A 1AA", "+919876543210", "new delhi", None)


def test_address_line_2_is_optional(client, profile):
    body = {k: v for k, v in HOME.items() if k != "address_line_2"}
    assert add(client, body)["address_line_2"] is None
