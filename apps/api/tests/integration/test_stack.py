import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
TARGET = os.environ.get("PLIMSOLL_TEST_TARGET_URL", "http://localhost:8080")


def test_api_is_live() -> None:
    assert httpx.get(f"{API}/healthz", timeout=5).json() == {"status": "ok"}


def test_api_is_ready_with_every_dependency_up() -> None:
    response = httpx.get(f"{API}/readyz", timeout=10)
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks == {"postgres": True, "redis": True, "objectstore": True}


def test_version_is_served() -> None:
    assert httpx.get(f"{API}/api/v1/version", timeout=5).json()["version"]


def test_demo_target_responds() -> None:
    assert httpx.get(f"{TARGET}/healthz", timeout=5).status_code == 200
