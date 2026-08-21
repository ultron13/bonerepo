"""Every state-changing route refuses a VIEWER, enumerated from the API itself.

Listing the endpoints by hand would pass forever the moment someone adds one
and forgets the guard, which is the failure this exists to catch. The
enumeration comes from the OpenAPI document rather than from FastAPI's route
objects: the document is the published contract, while the internal structure
of an included router changes between releases.

This checks behaviour, not decoration. A route missing `requires(...)` answers
a VIEWER with 200, 404, or 422 instead of 403, and fails here.
"""

import httpx
import pytest

from plimsoll_api.main import create_app

pytestmark = pytest.mark.integration

WRITE_METHODS = {"post", "patch", "put", "delete"}

# Login, refresh, and logout establish a principal and therefore cannot require
# one: they are authenticated by the credential in the request itself.
UNGUARDED_BY_DESIGN = {
    ("post", "/api/v1/auth/login"),
    ("post", "/api/v1/auth/refresh"),
    ("post", "/api/v1/auth/logout"),
}

PLACEHOLDER = "00000000-0000-0000-0000-000000000000"


def _write_operations() -> list[tuple[str, str]]:
    document = create_app().openapi()
    return [
        (method, path)
        for path, operations in document["paths"].items()
        for method in operations
        if method in WRITE_METHODS and (method, path) not in UNGUARDED_BY_DESIGN
    ]


def _concrete(path: str) -> str:
    """Path parameters get a syntactically valid value; authorisation is
    checked before the handler runs, so the target need not exist."""
    while "{" in path:
        start, end = path.index("{"), path.index("}")
        path = path[:start] + PLACEHOLDER + path[end + 1 :]
    return path


def test_the_enumeration_finds_the_write_routes() -> None:
    """Without this, an enumeration that silently returns nothing would make
    the sweep below pass forever."""
    operations = _write_operations()
    # A loose floor plus known members: a count pinned to today's number goes
    # stale every time a slice adds an endpoint, and stale gets deleted.
    assert len(operations) >= 5, operations
    assert ("post", "/api/v1/projects") in operations
    assert ("delete", "/api/v1/credentials/{credential_id}") in operations
    assert ("put", "/api/v1/target-policy") in operations


def test_a_viewer_is_refused_by_every_write(viewer_client: httpx.Client) -> None:
    allowed = []
    for method, path in _write_operations():
        response = viewer_client.request(method.upper(), _concrete(path), json={})
        if response.status_code != 403:
            allowed.append(f"{method.upper()} {path} -> {response.status_code}")
    assert allowed == [], f"writes a VIEWER was not refused: {allowed}"
