from fastapi.testclient import TestClient

from plimsoll_api.main import create_app
from plimsoll_api.routers.health import set_readiness_checks


class StubCheck:
    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self._healthy = healthy

    async def check(self) -> bool:
        return self._healthy


def test_healthz_is_always_ok_and_checks_nothing() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", False)])
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_ok_when_every_dependency_is_up() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", True), StubCheck("redis", True)])
    response = TestClient(app).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"postgres": True, "redis": True}}


def test_readyz_is_503_when_a_dependency_is_down() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", True), StubCheck("redis", False)])
    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": True, "redis": False}


def test_version_reports_build_metadata() -> None:
    response = TestClient(create_app()).get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "gitSha" in body
