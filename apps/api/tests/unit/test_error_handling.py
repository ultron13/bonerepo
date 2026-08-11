from fastapi.testclient import TestClient
from pydantic import BaseModel

from plimsoll_api.main import create_app


def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_unknown_route_returns_the_error_envelope() -> None:
    response = client().get("/api/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["requestId"]


def test_request_id_is_echoed_in_the_header() -> None:
    response = client().get("/api/v1/nope", headers={"X-Request-ID": "req-supplied"})
    assert response.headers["x-request-id"] == "req-supplied"
    assert response.json()["error"]["requestId"] == "req-supplied"


def test_request_id_is_generated_when_absent() -> None:
    response = client().get("/api/v1/nope")
    assert response.headers["x-request-id"]


def test_an_unhandled_exception_still_carries_the_request_id() -> None:
    app = create_app()

    @app.get("/api/v1/_test-boom")
    async def _boom() -> None:
        raise RuntimeError("boom")

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/_test-boom", headers={"X-Request-ID": "req-boom"}
    )
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL"
    assert body["error"]["requestId"] == "req-boom"


def test_validation_failures_report_every_problem_at_once() -> None:
    app = create_app()

    class Body(BaseModel):
        a: int
        b: int

    @app.post("/api/v1/_test-validation")
    async def _endpoint(body: Body) -> dict[str, int]:
        return {"ok": 1}

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/_test-validation", json={"a": "x", "b": "y"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert len(body["error"]["details"]["fields"]) == 2
