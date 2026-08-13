import uuid

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plimsoll_api.errors import register_error_handlers
from plimsoll_api.security.permissions import Permission, permissions_for, requires
from plimsoll_api.security.tokens import issue_access_token


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/guarded", dependencies=[Depends(requires(Permission.PROJECT_WRITE))])
    def guarded() -> dict[str, bool]:
        return {"ok": True}

    return app


def _token(role: str) -> str:
    return issue_access_token(uuid.uuid4(), uuid.uuid4(), role)


def test_an_admin_holds_every_permission() -> None:
    assert permissions_for("ORG_ADMIN") == frozenset(Permission)


def test_a_viewer_holds_only_reads() -> None:
    assert permissions_for("VIEWER") == frozenset(
        {Permission.PROJECT_READ, Permission.SCRIPT_READ, Permission.TEST_READ}
    )


def test_an_unknown_role_holds_nothing() -> None:
    assert permissions_for("SOMETHING_NEW") == frozenset()


@pytest.mark.parametrize(("role", "status"), [("ORG_ADMIN", 200), ("VIEWER", 403)])
def test_the_guard_enforces_the_permission(role: str, status: int) -> None:
    client = TestClient(_app())
    response = client.post("/guarded", headers={"Authorization": f"Bearer {_token(role)}"})
    assert response.status_code == status


def test_the_guard_rejects_an_anonymous_caller() -> None:
    assert TestClient(_app()).post("/guarded").status_code == 401
