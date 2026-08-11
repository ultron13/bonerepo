import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
ADMIN = {"email": "admin@demo.plimsoll.dev", "password": "plimsoll-demo-password"}


def test_login_returns_an_access_token() -> None:
    response = httpx.post(f"{API}/api/v1/auth/login", json=ADMIN, timeout=10)
    assert response.status_code == 200
    assert response.json()["accessToken"]


def test_login_with_a_wrong_password_is_unauthenticated() -> None:
    response = httpx.post(
        f"{API}/api/v1/auth/login",
        json={**ADMIN, "password": "wrong"},
        timeout=10,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_login_with_an_unknown_email_is_unauthenticated() -> None:
    response = httpx.post(
        f"{API}/api/v1/auth/login",
        json={"email": "nobody@demo.plimsoll.dev", "password": "whatever"},
        timeout=10,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_me_requires_a_token() -> None:
    assert httpx.get(f"{API}/api/v1/auth/me", timeout=10).status_code == 401


def test_me_returns_the_principal() -> None:
    token = httpx.post(f"{API}/api/v1/auth/login", json=ADMIN, timeout=10).json()["accessToken"]
    response = httpx.get(
        f"{API}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == ADMIN["email"]
    assert body["orgRole"] == "ORG_ADMIN"


def test_refresh_rotates_and_reuse_is_rejected() -> None:
    with httpx.Client(base_url=API, timeout=10) as client:
        client.post("/api/v1/auth/login", json=ADMIN)
        first_cookie = client.cookies.get("plimsoll_refresh")
        assert first_cookie

        assert client.post("/api/v1/auth/refresh").status_code == 200
        assert client.cookies.get("plimsoll_refresh") != first_cookie

        client.cookies.set("plimsoll_refresh", first_cookie)
        assert client.post("/api/v1/auth/refresh").status_code == 401


def test_logout_clears_the_refresh_cookie() -> None:
    with httpx.Client(base_url=API, timeout=10) as client:
        client.post("/api/v1/auth/login", json=ADMIN)
        assert client.cookies.get("plimsoll_refresh")
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert not client.cookies.get("plimsoll_refresh")
