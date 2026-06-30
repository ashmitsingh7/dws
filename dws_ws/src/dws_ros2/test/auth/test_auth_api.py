import sys
import os

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_BUILD_DIR = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, os.path.join(_BUILD_DIR, "authentication"))
sys.path.insert(0, os.path.join(_BUILD_DIR, "database"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def api_client(migrated_db, monkeypatch):
    db_path, admin_password = migrated_db
    monkeypatch.setenv("DWS_DB_PATH", db_path)

    # auth_api resolves DB_PATH at import time, so re-import fresh per
    # test with the env var already set, to make sure each test talks
    # to its own isolated database file.
    for mod_name in ("auth_api",):
        sys.modules.pop(mod_name, None)

    from auth_api import auth_router

    app = FastAPI()
    app.include_router(auth_router)
    client = TestClient(app)
    return client, admin_password


def _login(client, username, password):
    r = client.post("/auth/login", json={"username": username, "password": password})
    return r


def test_login_endpoint_success(api_client):
    client, admin_password = api_client
    r = _login(client, "admin", admin_password)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "administrator"
    assert body["must_change_password"] is True
    assert "token" in body


def test_login_endpoint_wrong_password(api_client):
    client, _ = api_client
    r = _login(client, "admin", "wrong-password")
    assert r.status_code == 401


def test_session_endpoint_requires_auth(api_client):
    client, _ = api_client
    r = client.get("/auth/session")
    assert r.status_code == 401


def test_session_endpoint_returns_permissions(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    r = client.get("/auth/session", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "users.manage" in r.json()["permissions"]


def test_create_user_requires_permission(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]

    r = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": "newop", "password": "StrongPass123", "role": "operator"},
    )
    assert r.status_code == 201
    assert r.json()["role"] == "operator"


def test_operator_cannot_create_users(api_client):
    client, admin_password = api_client
    admin_token = _login(client, "admin", admin_password).json()["token"]
    client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"username": "op1", "password": "StrongPass123", "role": "operator"},
    )

    op_token = _login(client, "op1", "StrongPass123").json()["token"]
    r = client.post(
        "/admin/users",
        headers={"Authorization": f"Bearer {op_token}"},
        json={"username": "op2", "password": "StrongPass123", "role": "operator"},
    )
    assert r.status_code == 403


def test_duplicate_username_returns_409(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/admin/users", headers=headers,
                json={"username": "dupe", "password": "StrongPass123", "role": "operator"})
    r = client.post("/admin/users", headers=headers,
                     json={"username": "dupe", "password": "AnotherPass456", "role": "operator"})
    assert r.status_code == 409


def test_weak_password_returns_400(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post("/admin/users", headers=headers,
                     json={"username": "weak", "password": "weak", "role": "operator"})
    assert r.status_code == 400


def test_disable_user_invalidates_their_active_session(api_client):
    client, admin_password = api_client
    admin_token = _login(client, "admin", admin_password).json()["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    created = client.post(
        "/admin/users", headers=admin_headers,
        json={"username": "op1", "password": "StrongPass123", "role": "operator"},
    ).json()

    op_token = _login(client, "op1", "StrongPass123").json()["token"]
    op_headers = {"Authorization": f"Bearer {op_token}"}
    assert client.get("/auth/session", headers=op_headers).status_code == 200

    r = client.post(f"/admin/users/{created['id']}/disable", headers=admin_headers)
    assert r.status_code == 200

    # Old session must be dead immediately.
    assert client.get("/auth/session", headers=op_headers).status_code == 401

    # And login must now be rejected outright.
    assert _login(client, "op1", "StrongPass123").status_code == 403


def test_admin_cannot_delete_own_account(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/auth/session", headers=headers).json()
    r = client.delete(f"/admin/users/{me['user_id']}", headers=headers)
    assert r.status_code == 400


def test_logout_invalidates_token(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/auth/session", headers=headers).status_code == 200
    r = client.post("/auth/logout", headers=headers)
    assert r.status_code == 200
    assert client.get("/auth/session", headers=headers).status_code == 401


def test_change_password_endpoint(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        "/auth/change-password", headers=headers,
        json={"current_password": admin_password, "new_password": "BrandNewPass456"},
    )
    assert r.status_code == 200

    # Old token revoked as part of password change.
    assert client.get("/auth/session", headers=headers).status_code == 401

    # New password works.
    r2 = _login(client, "admin", "BrandNewPass456")
    assert r2.status_code == 200


def test_change_password_wrong_current_password_returns_401(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        "/auth/change-password", headers=headers,
        json={"current_password": "totally-wrong", "new_password": "BrandNewPass456"},
    )
    assert r.status_code == 401


def test_list_roles_endpoint(api_client):
    client, admin_password = api_client
    token = _login(client, "admin", admin_password).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/admin/roles", headers=headers)
    assert r.status_code == 200
    names = {role["name"] for role in r.json()}
    assert names == {"operator", "supervisor", "administrator"}
