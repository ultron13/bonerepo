import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration


def _name() -> str:
    return f"pool-{uuid.uuid4().hex[:8]}"


def _body(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "runtime": "docker",
        "config": {"image": "ghcr.io/ultron13/generator:jmeter-5.6.3"},
        "maxGenerators": 4,
        "maxVusPerGenerator": 500,
    }


def test_a_pool_is_created_with_its_capacity(admin_client: httpx.Client) -> None:
    response = admin_client.post("/api/v1/generator-pools", json=_body(_name()))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["maxGenerators"] == 4
    assert body["capacity"] == 2000
    assert body["supportedEngines"] == ["jmeter"]


def test_the_seeded_pool_is_present(admin_client: httpx.Client) -> None:
    names = [
        item["name"]
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
    ]
    assert "local-docker" in names


def test_an_unknown_runtime_is_rejected(admin_client: httpx.Client) -> None:
    body = _body(_name()) | {"runtime": "mainframe"}
    response = admin_client.post("/api/v1/generator-pools", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_zero_capacity_is_rejected(admin_client: httpx.Client) -> None:
    body = _body(_name()) | {"maxVusPerGenerator": 0}
    assert admin_client.post("/api/v1/generator-pools", json=body).status_code == 422


def test_a_duplicate_name_is_refused(admin_client: httpx.Client) -> None:
    name = _name()
    assert admin_client.post("/api/v1/generator-pools", json=_body(name)).status_code == 201
    response = admin_client.post("/api/v1/generator-pools", json=_body(name))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_a_pool_is_updated_and_archived(admin_client: httpx.Client) -> None:
    created = admin_client.post("/api/v1/generator-pools", json=_body(_name())).json()
    updated = admin_client.patch(
        f"/api/v1/generator-pools/{created['id']}", json={"maxGenerators": 8}
    )
    assert updated.status_code == 200
    assert updated.json()["capacity"] == 4000

    assert admin_client.delete(f"/api/v1/generator-pools/{created['id']}").status_code == 204
    archived = admin_client.get(f"/api/v1/generator-pools/{created['id']}")
    assert archived.json()["status"] == "ARCHIVED"


def test_a_viewer_cannot_create_one(viewer_client: httpx.Client) -> None:
    assert viewer_client.post("/api/v1/generator-pools", json=_body(_name())).status_code == 403
