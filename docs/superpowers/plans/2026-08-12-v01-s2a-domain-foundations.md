# Plimsoll v0.1 Slice 2a — Domain Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every write endpoint in Plimsoll is authorised, audited, and tenant-scoped, and an operator can configure projects, credentials, generator pools, and the target policy over HTTP.

**Architecture:** The S1 spine continues — `router → service → repository → model`, DTOs at the boundary, sessions taken through `TenantSession` so row-level security scopes every query and the transaction commits before the response. This plan adds three cross-cutting pieces first (permissions, audit, the key provider), then four CRUD verticals that use all three.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (Core `text()` in repositories, as S1 does), Pydantic v2, `cryptography` for AES-256-GCM, pytest.

Design: [`docs/superpowers/specs/2026-08-12-v01-s2-domain-and-git-design.md`](../specs/2026-08-12-v01-s2-domain-and-git-design.md).

## Global Constraints

- Python 3.12+. Format with `ruff format`; lint with `ruff` (`E,F,I,UP,B,ASYNC,S,RUF`, line length 100). `mypy --strict` must pass.
- **No migration in this plan.** Every table already exists. If a column appears to be missing, stop and report it — do not add one.
- Repositories use `sa.text()` with bind parameters. Never f-string SQL.
- Every endpoint takes its session through `TenantSession` / `AnonymousSession` from `plimsoll_api.dependencies`. A bare `Depends(tenant_session)` commits after the response is sent and is a bug.
- Every write route carries `dependencies=[Depends(requires(...))]`. Every state change writes an audit row in the same transaction.
- Timestamps are `TIMESTAMPTZ`, UTC, ISO-8601 at the boundary.
- Contracts are camelCase on the wire via `serialization_alias`, snake_case in Python.
- **Any task that changes the API surface runs `make contracts` before committing and commits the regenerated `packages/contracts/typescript/src/generated.ts`.** `test_regeneration_leaves_the_tree_clean` fails otherwise.
- Tests accompany behaviour changes in the same commit. Commit messages are signed off (`git commit -s`) and end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Cross-organisation access returns `404`, never `403`. A `VIEWER` attempting a write within its own organisation gets `403`.

## File Structure

```
apps/api/plimsoll_api/
  security/permissions.py      Permission enum, role map, requires() route guard
  security/secrets.py          KeyProvider protocol, LocalKeyProvider, get_key_provider()
  services/audit.py            record() — an audit row inside the caller's transaction
  services/projects.py         Project rules: key uniqueness, soft delete
  services/credentials.py      Encrypt on create, refuse deletion while referenced
  services/pools.py            Pool rules: capacity fields, soft delete
  services/target_policy.py    Entry validation, allowlist matching, version insert
  repositories/projects.py     Keyset-paged reads and writes
  repositories/credentials.py
  repositories/pools.py
  repositories/target_policy.py
  repositories/audit.py
  routers/projects.py routers/credentials.py routers/pools.py routers/target_policy.py
packages/contracts/python/plimsoll_contracts/
  common.py                    Shared DTO base and the soft-delete status enum
  projects.py credentials.py pools.py policy.py
apps/api/tests/unit/
  test_permissions.py test_secrets.py test_target_policy_rules.py
apps/api/tests/integration/
  conftest.py                  (modified) authenticated client fixtures
  test_audit.py test_projects_api.py test_credentials_api.py
  test_pools_api.py test_target_policy_api.py test_write_authorisation.py
```

---

### Task 1: The permission catalogue and route guard

**Files:**
- Create: `apps/api/plimsoll_api/security/permissions.py`
- Test: `apps/api/tests/unit/test_permissions.py`

**Interfaces:**
- Consumes: `CurrentPrincipal` from `plimsoll_api.dependencies`, `PlimsollError`, `ErrorCode`.
- Produces: `Permission` (StrEnum), `permissions_for(role: str) -> frozenset[Permission]`, `requires(permission: Permission) -> Callable[..., None]` for use as `dependencies=[Depends(requires(Permission.PROJECT_WRITE))]`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/test_permissions.py`:

```python
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from plimsoll_api.errors import register_error_handlers
from plimsoll_api.security.permissions import Permission, permissions_for, requires
from plimsoll_api.security.tokens import issue_access_token
import uuid


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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_permissions.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.security.permissions`.

- [ ] **Step 3: Write the module**

`apps/api/plimsoll_api/security/permissions.py`:

```python
"""What a principal may do, checked server-side on every route.

The names are the ones the data model already documents. v0.1 issues two roles;
the v0.3 matrix changes this map and the way a principal's roles are resolved,
not the endpoints that consume it.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from plimsoll_api.dependencies import CurrentPrincipal
from plimsoll_api.errors import PlimsollError
from plimsoll_contracts.errors import ErrorCode


class Permission(StrEnum):
    PROJECT_READ = "project.read"
    PROJECT_WRITE = "project.write"
    SCRIPT_READ = "script.read"
    SCRIPT_WRITE = "script.write"
    TEST_READ = "test.read"
    TEST_WRITE = "test.write"
    ADMIN_SYSTEM = "admin.system"


READ_PERMISSIONS = frozenset(
    {Permission.PROJECT_READ, Permission.SCRIPT_READ, Permission.TEST_READ}
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "ORG_ADMIN": frozenset(Permission),
    "VIEWER": READ_PERMISSIONS,
}


def permissions_for(role: str) -> frozenset[Permission]:
    """An unrecognised role holds nothing, so a widened enum fails closed."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def requires(permission: Permission) -> Callable[..., None]:
    def guard(principal: CurrentPrincipal) -> None:
        if permission not in permissions_for(principal.role):
            raise PlimsollError(
                ErrorCode.PERMISSION_DENIED,
                f"This action requires the {permission} permission.",
            )

    return guard
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_permissions.py -v`
Expected: PASS — six tests.

- [ ] **Step 5: Commit**

```bash
git add apps/api/plimsoll_api/security/permissions.py apps/api/tests/unit/test_permissions.py
git commit -s -m "feat(security): permission catalogue and the route guard"
```

---

### Task 2: Audit records inside the caller's transaction

**Files:**
- Create: `apps/api/plimsoll_api/repositories/audit.py`, `apps/api/plimsoll_api/services/audit.py`
- Test: `apps/api/tests/integration/test_audit.py`

**Interfaces:**
- Consumes: `AccessClaims`, `session_for_org`.
- Produces: `audit.record(session, *, principal, action, entity_type, entity_id, metadata=None) -> None`. Actions are dotted strings: `project.created`, `project.updated`, `project.deleted`, `credential.created`, `credential.deleted`, `pool.created`, `pool.updated`, `pool.deleted`, `target_policy.updated`, `script_repo.created`, `script_repo.updated`, `script_repo.deleted`, `script_repo.verified`, `script_version.pinned`, `test.created`, `test.updated`, `test.deleted`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_audit.py`:

```python
"""An audited change and the record of it commit together or not at all."""

import uuid

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit

pytestmark = pytest.mark.integration

ORG = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.plimsoll.dev")


def _principal() -> AccessClaims:
    return AccessClaims(user_id=uuid.uuid4(), organization_id=ORG, role="ORG_ADMIN")


async def _count(action: str, entity_id: uuid.UUID) -> int:
    async with session_for_org(ORG) as session:
        count: int = await session.scalar(
            sa.text(
                "SELECT count(*) FROM audit_logs WHERE action = :action AND entity_id = :entity"
            ),
            {"action": action, "entity": entity_id},
        )
    return count


async def test_a_recorded_action_is_readable_after_commit() -> None:
    entity = uuid.uuid4()
    async with session_for_org(ORG) as session:
        await audit.record(
            session,
            principal=_principal(),
            action="project.created",
            entity_type="project",
            entity_id=entity,
            metadata={"name": "Audited"},
        )

    assert await _count("project.created", entity) == 1


async def test_a_rolled_back_change_leaves_no_audit_row() -> None:
    entity = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with session_for_org(ORG) as session:
            await audit.record(
                session,
                principal=_principal(),
                action="project.created",
                entity_type="project",
                entity_id=entity,
            )
            raise RuntimeError("the change failed after the audit row was written")

    assert await _count("project.created", entity) == 0
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `make dev && uv run pytest apps/api/tests/integration/test_audit.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.services.audit`.

- [ ] **Step 3: Write the repository**

`apps/api/plimsoll_api/repositories/audit.py`:

```python
from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO audit_logs "
            "(id, organization_id, user_id, action, entity_type, entity_id, metadata) "
            "VALUES (:id, :org, :user, :action, :entity_type, :entity_id, "
            "CAST(:metadata AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "org": org_id,
            "user": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": json.dumps(metadata) if metadata is not None else None,
        },
    )
```

- [ ] **Step 4: Write the service**

`apps/api/plimsoll_api/services/audit.py`:

```python
"""The audit trail is written inside the caller's transaction.

A trail written in a second transaction has holes in it exactly when something
went wrong -- the change rolls back and its record survives, or the reverse.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.repositories import audit as repo
from plimsoll_api.security.tokens import AccessClaims


async def record(
    session: AsyncSession,
    *,
    principal: AccessClaims,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await repo.insert(
        session,
        org_id=principal.organization_id,
        user_id=principal.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_audit.py -v -m integration`
Expected: PASS — both tests.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plimsoll_api/repositories/audit.py apps/api/plimsoll_api/services/audit.py \
        apps/api/tests/integration/test_audit.py
git commit -s -m "feat(audit): state changes and their record commit together"
```

---

### Task 3: The key provider

**Files:**
- Create: `apps/api/plimsoll_api/security/secrets.py`
- Modify: `apps/api/plimsoll_api/config.py`, `apps/api/pyproject.toml`, `apps/api/tests/conftest.py`, `infrastructure/docker/docker-compose.yml`, `.env.example`
- Test: `apps/api/tests/unit/test_secrets.py`

**Interfaces:**
- Produces: `KeyProvider` protocol with `encrypt(plaintext: bytes) -> tuple[bytes, str]` and `decrypt(ciphertext: bytes, key_ref: str) -> bytes`; `LocalKeyProvider`; `SecretError`; `get_key_provider() -> KeyProvider`.
- Settings gains `credential_key: str` (required, no default).

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/test_secrets.py`:

```python
import base64
import os

import pytest

from plimsoll_api.security.secrets import LocalKeyProvider, SecretError


def _provider() -> LocalKeyProvider:
    return LocalKeyProvider(base64.urlsafe_b64encode(b"k" * 32).decode())


def test_a_secret_survives_a_round_trip() -> None:
    provider = _provider()
    ciphertext, key_ref = provider.encrypt(b"ghp_notarealtoken")
    assert provider.decrypt(ciphertext, key_ref) == b"ghp_notarealtoken"


def test_the_key_reference_names_the_provider_and_version() -> None:
    assert _provider().encrypt(b"x")[1] == "local:v1"


def test_the_same_plaintext_encrypts_differently_each_time() -> None:
    provider = _provider()
    assert provider.encrypt(b"x")[0] != provider.encrypt(b"x")[0]


def test_a_tampered_ciphertext_is_refused() -> None:
    provider = _provider()
    ciphertext, key_ref = provider.encrypt(b"secret")
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0x01
    with pytest.raises(SecretError):
        provider.decrypt(bytes(tampered), key_ref)


def test_another_key_cannot_read_it() -> None:
    ciphertext, key_ref = _provider().encrypt(b"secret")
    other = LocalKeyProvider(base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(SecretError):
        other.decrypt(ciphertext, key_ref)


def test_an_unknown_key_reference_is_refused() -> None:
    provider = _provider()
    ciphertext, _ = provider.encrypt(b"secret")
    with pytest.raises(SecretError):
        provider.decrypt(ciphertext, "vault:prod-2")


def test_a_short_key_is_rejected_at_construction() -> None:
    with pytest.raises(SecretError):
        LocalKeyProvider(base64.urlsafe_b64encode(b"tooshort").decode())
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.security.secrets`.

- [ ] **Step 3: Add the dependency and the setting**

In `apps/api/pyproject.toml`, add `"cryptography>=43.0"` to `dependencies`. Then:

```bash
uv sync
```

In `apps/api/plimsoll_api/config.py`, add below `jwt_secret`:

```python
    # Encrypts the credentials table. No default: a built-in key is the same as
    # no encryption, discovered later.
    credential_key: str
```

- [ ] **Step 4: Write the module**

`apps/api/plimsoll_api/security/secrets.py`:

```python
"""Encryption at rest for the credentials table.

The provider is a seam. v0.1 has one implementation reading a key from the
environment; Vault and KMS are later implementations and later key_ref values,
which is why the reference is stored per row rather than assumed globally.
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from plimsoll_api.config import get_settings

NONCE_BYTES = 12
KEY_BYTES = 32


class SecretError(Exception):
    """The key is unusable, or the ciphertext does not belong to it."""


class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]: ...

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes: ...


class LocalKeyProvider:
    """AES-256-GCM under a key supplied by the environment.

    The nonce is stored in front of the ciphertext: it is not secret, it must
    never repeat under one key, and keeping it with the row removes any chance
    of pairing the wrong one at decryption.
    """

    KEY_REF = "local:v1"

    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.urlsafe_b64decode(encoded_key)
        except (binascii.Error, ValueError) as exc:
            raise SecretError("PLIMSOLL_CREDENTIAL_KEY is not valid base64.") from exc
        if len(key) != KEY_BYTES:
            raise SecretError(
                f"PLIMSOLL_CREDENTIAL_KEY must decode to {KEY_BYTES} bytes, got {len(key)}."
            )
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._cipher.encrypt(nonce, plaintext, None), self.KEY_REF

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes:
        if key_ref != self.KEY_REF:
            raise SecretError(f"No provider is configured for key reference {key_ref}.")
        nonce, body = ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:]
        try:
            return self._cipher.decrypt(nonce, body, None)
        except InvalidTag as exc:
            raise SecretError("The ciphertext failed authentication.") from exc


@lru_cache
def get_key_provider() -> KeyProvider:
    return LocalKeyProvider(get_settings().credential_key)
```

- [ ] **Step 5: Give every environment a key**

`apps/api/tests/conftest.py`, alongside the other defaults:

```python
os.environ.setdefault(
    "PLIMSOLL_CREDENTIAL_KEY", base64.urlsafe_b64encode(b"unit-test-key-32-bytes-exactly!!").decode()
)
```

with `import base64` at the top. Confirm the literal is exactly 32 bytes before committing:
`python -c "print(len(b'unit-test-key-32-bytes-exactly!!'))"` must print `32`.

In `infrastructure/docker/docker-compose.yml`, under the `api` service environment, next to `PLIMSOLL_JWT_SECRET`:

```yaml
      PLIMSOLL_CREDENTIAL_KEY: ZGV2ZWxvcG1lbnQtb25seS1rZXktMzItYnl0ZXMtbG9uZyE=
```

Add the same key to `.env.example` with a comment saying it is development-only and that a deployment generates its own with
`python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_secrets.py -v` — seven tests PASS.
Then `make dev` and `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/healthz` returns `200`, proving the container starts with the new required setting.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(security): AES-256-GCM credential encryption behind a key provider"
```

---

### Task 4: Projects

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/projects.py`, `apps/api/plimsoll_api/repositories/projects.py`, `apps/api/plimsoll_api/services/projects.py`, `apps/api/plimsoll_api/routers/projects.py`
- Modify: `apps/api/plimsoll_api/main.py`, `apps/api/tests/integration/conftest.py`
- Test: `apps/api/tests/integration/test_projects_api.py`

**Interfaces:**
- Consumes: `TenantSession`, `CurrentPrincipal`, `requires`, `audit.record`, `Page`/`encode_cursor`/`decode_cursor`.
- Produces: contracts `ProjectCreate`, `ProjectUpdate`, `ProjectResponse`; repository functions `insert`, `get`, `list_page`, `update`, `archive`; the fixtures `admin_client`, `viewer_client`, `demo_project_id` for every later task's integration tests.

- [ ] **Step 1: Write the failing test**

First the shared fixtures. Append to `apps/api/tests/integration/conftest.py`:

```python
import httpx

API_URL = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
ADMIN = {"email": "admin@demo.plimsoll.dev", "password": "plimsoll-demo-password"}
VIEWER = {"email": "viewer@demo.plimsoll.dev", "password": "plimsoll-demo-password"}


def _signed_in(account: dict[str, str]) -> httpx.Client:
    client = httpx.Client(base_url=API_URL, timeout=30)
    token = client.post("/api/v1/auth/login", json=account).json()["accessToken"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


@pytest.fixture
def admin_client() -> Iterator[httpx.Client]:
    with _signed_in(ADMIN) as client:
        yield client


@pytest.fixture
def viewer_client() -> Iterator[httpx.Client]:
    with _signed_in(VIEWER) as client:
        yield client
```

with `from collections.abc import AsyncIterator, Iterator` replacing the existing `AsyncIterator` import.

`apps/api/tests/integration/test_projects_api.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _create(client: httpx.Client, key: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/projects",
        json={"name": f"Project {key}", "projectKey": key, "environment": "staging"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _unique_key() -> str:
    return f"K{uuid.uuid4().hex[:8].upper()}"


def test_a_project_is_created_and_read_back(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    fetched = admin_client.get(f"/api/v1/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == created["name"]
    assert fetched.json()["status"] == "ACTIVE"


def test_a_duplicate_project_key_is_refused(admin_client: httpx.Client) -> None:
    key = _unique_key()
    _create(admin_client, key)
    response = admin_client.post(
        "/api/v1/projects", json={"name": "Second", "projectKey": key}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_the_list_is_paged(admin_client: httpx.Client) -> None:
    for _ in range(3):
        _create(admin_client, _unique_key())
    page = admin_client.get("/api/v1/projects?limit=2").json()
    assert len(page["items"]) == 2
    assert page["nextCursor"]
    following = admin_client.get(f"/api/v1/projects?limit=2&cursor={page['nextCursor']}").json()
    first_ids = {item["id"] for item in page["items"]}
    assert first_ids.isdisjoint({item["id"] for item in following["items"]})


def test_a_project_is_updated(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    response = admin_client.patch(
        f"/api/v1/projects/{created['id']}", json={"description": "Now described"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Now described"


def test_deleting_archives_rather_than_removes(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    assert admin_client.delete(f"/api/v1/projects/{created['id']}").status_code == 204
    fetched = admin_client.get(f"/api/v1/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "ARCHIVED"


def test_an_unknown_project_is_not_found(admin_client: httpx.Client) -> None:
    response = admin_client.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_viewer_may_read_but_not_write(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    created = _create(admin_client, _unique_key())
    assert viewer_client.get(f"/api/v1/projects/{created['id']}").status_code == 200
    refused = viewer_client.post(
        "/api/v1/projects", json={"name": "Nope", "projectKey": _unique_key()}
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "PERMISSION_DENIED"


def test_creating_a_project_writes_an_audit_row(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    # Read through the API's own tenancy: the audit table is organisation-scoped.
    import sqlalchemy as sa

    from plimsoll_api.db.session import session_for_org

    async def count() -> int:
        org = uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])
        async with session_for_org(org) as session:
            value: int = await session.scalar(
                sa.text("SELECT count(*) FROM audit_logs WHERE entity_id = :id"),
                {"id": uuid.UUID(str(created["id"]))},
            )
        return value

    import anyio

    assert anyio.run(count) == 1
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_projects_api.py -v -m integration`
Expected: FAIL — `404` from `POST /api/v1/projects`.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/projects.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    project_key: str = Field(
        min_length=1, max_length=50, pattern=r"^[A-Z][A-Z0-9_]*$", alias="projectKey"
    )
    description: str | None = None
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    project_key: str = Field(serialization_alias="projectKey")
    description: str | None
    environment: str | None
    status: str
    tags: list[str]
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
```

- [ ] **Step 4: Write the repository**

`apps/api/plimsoll_api/repositories/projects.py`:

```python
"""Keyset pagination on (created_at, id).

Offset pagination skips or duplicates rows when inserts land mid-scan, which on
a control plane whose history only grows is always.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    "id, name, project_key, description, environment, status, tags, created_at, updated_at"
)


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    project_key: str,
    description: str | None,
    environment: str | None,
    tags: list[str],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO projects "
                "(id, organization_id, name, project_key, description, environment, tags, "
                " created_by) "
                "VALUES (:id, :org, :name, :key, :description, :environment, :tags, :by) "
                f"RETURNING {COLUMNS}"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "key": project_key,
                "description": description,
                "environment": environment,
                "tags": tags,
                "by": created_by,
            },
        )
    ).one()


async def get(session: AsyncSession, project_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(f"SELECT {COLUMNS} FROM projects WHERE id = :id"), {"id": project_id}
        )
    ).first()


async def list_page(
    session: AsyncSession, *, limit: int, after: tuple[datetime, uuid.UUID] | None
) -> list[sa.Row[Any]]:
    clause = "" if after is None else "WHERE (created_at, id) < (:after_at, :after_id) "
    parameters: dict[str, Any] = {"limit": limit}
    if after is not None:
        parameters["after_at"], parameters["after_id"] = after
    result = await session.execute(
        sa.text(
            f"SELECT {COLUMNS} FROM projects {clause}"
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        parameters,
    )
    return list(result.all())


async def update(
    session: AsyncSession, project_id: uuid.UUID, changes: dict[str, Any]
) -> sa.Row[Any] | None:
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    return (
        await session.execute(
            sa.text(
                f"UPDATE projects SET {assignments}, updated_at = now() "
                f"WHERE id = :id RETURNING {COLUMNS}"
            ),
            {**changes, "id": project_id},
        )
    ).first()


async def archive(session: AsyncSession, project_id: uuid.UUID) -> bool:
    result = await session.execute(
        sa.text(
            "UPDATE projects SET status = 'ARCHIVED', updated_at = now() "
            "WHERE id = :id AND status <> 'ARCHIVED'"
        ),
        {"id": project_id},
    )
    return result.rowcount > 0
```

The column names in `changes` come from a fixed mapping in the service, never from the request body — an assignment list built from client keys is SQL injection with extra steps.

- [ ] **Step 5: Write the service**

`apps/api/plimsoll_api/services/projects.py`:

```python
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import projects as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.projects import ProjectCreate, ProjectUpdate

UPDATABLE = {"name", "description", "environment", "tags"}


async def create(
    session: AsyncSession, principal: AccessClaims, body: ProjectCreate
) -> Any:
    try:
        row = await repo.insert(
            session,
            org_id=principal.organization_id,
            created_by=principal.user_id,
            name=body.name,
            project_key=body.project_key,
            description=body.description,
            environment=body.environment,
            tags=body.tags,
        )
    except IntegrityError as exc:
        raise PlimsollError(
            ErrorCode.CONFLICT,
            f"A project with key {body.project_key} already exists.",
        ) from exc
    await audit.record(
        session,
        principal=principal,
        action="project.created",
        entity_type="project",
        entity_id=row.id,
        metadata={"projectKey": body.project_key},
    )
    return row


async def require(session: AsyncSession, project_id: uuid.UUID) -> Any:
    """A project in another organisation is invisible under RLS, so absent and
    forbidden are the same 404 -- confirming an identifier exists elsewhere is
    itself a leak."""
    row = await repo.get(session, project_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such project.")
    return row


async def update(
    session: AsyncSession, principal: AccessClaims, project_id: uuid.UUID, body: ProjectUpdate
) -> Any:
    await require(session, project_id)
    changes = {
        column: value
        for column, value in body.model_dump(exclude_unset=True).items()
        if column in UPDATABLE
    }
    if not changes:
        return await require(session, project_id)
    row = await repo.update(session, project_id, changes)
    await audit.record(
        session,
        principal=principal,
        action="project.updated",
        entity_type="project",
        entity_id=project_id,
        metadata={"fields": sorted(changes)},
    )
    return row


async def archive(
    session: AsyncSession, principal: AccessClaims, project_id: uuid.UUID
) -> None:
    await require(session, project_id)
    if await repo.archive(session, project_id):
        await audit.record(
            session,
            principal=principal,
            action="project.deleted",
            entity_type="project",
            entity_id=project_id,
        )
```

Archiving an already-archived project writes no second audit row: the `DELETE` is idempotent and repeating it has no side effect.

- [ ] **Step 6: Write the router**

`apps/api/plimsoll_api/routers/projects.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import projects as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import projects as service
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    Page,
    decode_cursor,
    encode_cursor,
)
from plimsoll_contracts.projects import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _response(row: Any) -> ProjectResponse:
    return ProjectResponse(
        id=row.id,
        name=row.name,
        project_key=row.project_key,
        description=row.description,
        environment=row.environment,
        status=row.status,
        tags=list(row.tags),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.PROJECT_WRITE))],
)
async def create_project(
    body: ProjectCreate, principal: CurrentPrincipal, session: TenantSession
) -> ProjectResponse:
    return _response(await service.create(session, principal, body))


@router.get(
    "", response_model=Page[ProjectResponse], dependencies=[Depends(requires(Permission.PROJECT_READ))]
)
async def list_projects(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[ProjectResponse]:
    after = None
    if cursor is not None:
        try:
            payload = decode_cursor(cursor)
            after = (datetime.fromisoformat(payload["createdAt"]), uuid.UUID(payload["id"]))
        except (ValueError, KeyError) as exc:
            raise PlimsollError(ErrorCode.VALIDATION_FAILED, "The cursor is malformed.") from exc

    rows = await repo.list_page(session, limit=limit + 1, after=after)
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(
            {"createdAt": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)}
        )
    return Page(items=[_response(row) for row in rows], next_cursor=next_cursor)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(requires(Permission.PROJECT_READ))],
)
async def get_project(project_id: uuid.UUID, session: TenantSession) -> ProjectResponse:
    return _response(await service.require(session, project_id))


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(requires(Permission.PROJECT_WRITE))],
)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> ProjectResponse:
    return _response(await service.update(session, principal, project_id, body))


@router.delete(
    "/{project_id}", status_code=204, dependencies=[Depends(requires(Permission.PROJECT_WRITE))]
)
async def delete_project(
    project_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.archive(session, principal, project_id)
    return Response(status_code=204)
```

Register it in `main.py`: import `projects` alongside `auth, health` and `app.include_router(projects.router)`.

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_projects_api.py -v -m integration`
Expected: PASS — eight tests. If the paging test fails with both pages sharing a row, the keyset comparison is wrong: `(created_at, id) < (:after_at, :after_id)` is a row-value comparison and must not be rewritten as two `AND`ed inequalities.

- [ ] **Step 8: Regenerate contracts and commit**

```bash
make contracts
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): projects, paged, authorised, and audited"
```

---

### Task 5: Credentials

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/credentials.py`, `apps/api/plimsoll_api/repositories/credentials.py`, `apps/api/plimsoll_api/services/credentials.py`, `apps/api/plimsoll_api/routers/credentials.py`
- Modify: `apps/api/plimsoll_api/main.py`, `packages/contracts/python/plimsoll_contracts/errors.py`, `docs/architecture/06-api.md`
- Test: `apps/api/tests/integration/test_credentials_api.py`

**Interfaces:**
- Consumes: `get_key_provider`, `TenantSession`, `requires`, `audit.record`.
- Produces: contracts `CredentialCreate`, `CredentialResponse`; `credentials.secret_for(session, credential_id) -> bytes | None` — the only decryption path, used by the Git client in S2b; error code `RESOURCE_IN_USE`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_credentials_api.py`:

```python
import uuid

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration

SECRET = "ghp_thisisnotarealtoken"


def _create(client: httpx.Client, name: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/credentials",
        json={"name": name, "kind": "GIT_TOKEN", "secret": SECRET},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _name() -> str:
    return f"cred-{uuid.uuid4().hex[:8]}"


def test_a_credential_is_created_without_echoing_the_secret(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    assert SECRET not in str(created)
    assert set(created) == {"id", "name", "kind", "createdAt"}


def test_no_endpoint_returns_the_secret(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    listed = admin_client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert SECRET not in listed.text
    assert admin_client.get(f"/api/v1/credentials/{created['id']}").status_code == 405


def test_the_stored_ciphertext_is_not_the_plaintext(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    org = uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])

    async def stored() -> bytes:
        async with session_for_org(org) as session:
            value: bytes = await session.scalar(
                sa.text("SELECT ciphertext FROM credentials WHERE id = :id"),
                {"id": uuid.UUID(str(created["id"]))},
            )
        return value

    import anyio

    assert SECRET.encode() not in anyio.run(stored)


def test_a_duplicate_name_is_refused(admin_client: httpx.Client) -> None:
    name = _name()
    _create(admin_client, name)
    response = admin_client.post(
        "/api/v1/credentials", json={"name": name, "kind": "GIT_TOKEN", "secret": SECRET}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_an_unused_credential_is_deleted(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    assert admin_client.delete(f"/api/v1/credentials/{created['id']}").status_code == 204
    assert all(item["id"] != created["id"] for item in admin_client.get("/api/v1/credentials").json()["items"])


def test_a_credential_in_use_cannot_be_deleted(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    project = admin_client.post(
        "/api/v1/projects",
        json={"name": "Holder", "projectKey": f"H{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    org = uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])

    async def attach() -> None:
        async with session_for_org(org) as session:
            await session.execute(
                sa.text(
                    "INSERT INTO script_repos "
                    "(id, organization_id, project_id, name, repo_url, plan_path, credential_id) "
                    "VALUES (:id, :org, :project, 'holder', 'http://example.invalid/r.git', "
                    "'perf/plan.jmx', :credential)"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": org,
                    "project": uuid.UUID(str(project["id"])),
                    "credential": uuid.UUID(str(created["id"])),
                },
            )

    import anyio

    anyio.run(attach)

    response = admin_client.delete(f"/api/v1/credentials/{created['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESOURCE_IN_USE"
    assert response.json()["error"]["details"]["scriptRepos"]


def test_a_viewer_cannot_create_one(viewer_client: httpx.Client) -> None:
    response = viewer_client.post(
        "/api/v1/credentials", json={"name": _name(), "kind": "GIT_TOKEN", "secret": SECRET}
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_credentials_api.py -v -m integration`
Expected: FAIL — `404` from `POST /api/v1/credentials`.

- [ ] **Step 3: Append the error code**

In `packages/contracts/python/plimsoll_contracts/errors.py`, add to `ErrorCode` and to `HTTP_STATUS_FOR_CODE`:

```python
    RESOURCE_IN_USE = "RESOURCE_IN_USE"
```

```python
    ErrorCode.RESOURCE_IN_USE: 409,
```

In `docs/architecture/06-api.md`, add the row to the error catalogue table:

```markdown
| `RESOURCE_IN_USE` | 409 | The resource is referenced by another and cannot be deleted; `details` names the referents |
```

and add a `### Credentials` section after `### Projects`:

```markdown
### Credentials
```http
GET    /credentials
POST   /credentials
DELETE /credentials/{id}
```

A credential is written once and never read back: the API returns id, name,
kind, and creation time, and no endpoint returns the secret. Deleting one that
a script repository still references returns `RESOURCE_IN_USE`.
```

- [ ] **Step 4: Write the contracts**

`packages/contracts/python/plimsoll_contracts/credentials.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CredentialKind(StrEnum):
    GIT_TOKEN = "GIT_TOKEN"
    GIT_SSH_KEY = "GIT_SSH_KEY"
    VARIABLE = "VARIABLE"


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: CredentialKind
    # Write-only. There is no response model carrying this field, and adding one
    # would break the guarantee that secrets are never returned by the API.
    secret: str = Field(min_length=1)


class CredentialResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    kind: CredentialKind
    created_at: datetime = Field(serialization_alias="createdAt")
```

`GITHUB_APP` and `KUBECONFIG` exist in the column's comment but are not offered by the API in v0.1: the first is a v0.2 integration, the second belongs to the Kubernetes runtime in S3.

- [ ] **Step 5: Write the repository**

`apps/api/plimsoll_api/repositories/credentials.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = "id, name, kind, created_at"


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    kind: str,
    ciphertext: bytes,
    key_ref: str,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO credentials "
                "(id, organization_id, name, kind, ciphertext, key_ref, created_by) "
                "VALUES (:id, :org, :name, :kind, :ciphertext, :key_ref, :by) "
                f"RETURNING {COLUMNS}"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "kind": kind,
                "ciphertext": ciphertext,
                "key_ref": key_ref,
                "by": created_by,
            },
        )
    ).one()


async def list_page(
    session: AsyncSession, *, limit: int, after: tuple[datetime, uuid.UUID] | None
) -> list[sa.Row[Any]]:
    clause = "" if after is None else "WHERE (created_at, id) < (:after_at, :after_id) "
    parameters: dict[str, Any] = {"limit": limit}
    if after is not None:
        parameters["after_at"], parameters["after_id"] = after
    result = await session.execute(
        sa.text(
            f"SELECT {COLUMNS} FROM credentials {clause}"
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ),
        parameters,
    )
    return list(result.all())


async def get(session: AsyncSession, credential_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(f"SELECT {COLUMNS} FROM credentials WHERE id = :id"), {"id": credential_id}
        )
    ).first()


async def secret_material(
    session: AsyncSession, credential_id: uuid.UUID
) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT ciphertext, key_ref, kind FROM credentials WHERE id = :id"),
            {"id": credential_id},
        )
    ).first()


async def by_name(session: AsyncSession, name: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT id, ciphertext, key_ref, kind FROM credentials WHERE name = :name"),
            {"name": name},
        )
    ).first()


async def referencing_repos(session: AsyncSession, credential_id: uuid.UUID) -> list[str]:
    result = await session.execute(
        sa.text("SELECT name FROM script_repos WHERE credential_id = :id ORDER BY name"),
        {"id": credential_id},
    )
    return [row.name for row in result.all()]


async def delete(session: AsyncSession, credential_id: uuid.UUID) -> bool:
    result = await session.execute(
        sa.text("DELETE FROM credentials WHERE id = :id"), {"id": credential_id}
    )
    return result.rowcount > 0
```

- [ ] **Step 6: Write the service**

`apps/api/plimsoll_api/services/credentials.py`:

```python
"""Secrets go in and never come out.

There is one decryption path -- secret_for -- and it returns bytes to server-
side callers. No DTO in this slice carries plaintext, which is what makes the
"never returned by the API" rule checkable rather than aspirational.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import credentials as repo
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.credentials import CredentialCreate
from plimsoll_contracts.errors import ErrorCode


async def create(
    session: AsyncSession, principal: AccessClaims, body: CredentialCreate
) -> Any:
    ciphertext, key_ref = get_key_provider().encrypt(body.secret.encode())
    try:
        row = await repo.insert(
            session,
            org_id=principal.organization_id,
            created_by=principal.user_id,
            name=body.name,
            kind=str(body.kind),
            ciphertext=ciphertext,
            key_ref=key_ref,
        )
    except IntegrityError as exc:
        raise PlimsollError(
            ErrorCode.CONFLICT, f"A credential named {body.name} already exists."
        ) from exc
    await audit.record(
        session,
        principal=principal,
        action="credential.created",
        entity_type="credential",
        entity_id=row.id,
        metadata={"kind": str(body.kind)},
    )
    return row


async def secret_for(session: AsyncSession, credential_id: uuid.UUID) -> bytes | None:
    material = await repo.secret_material(session, credential_id)
    if material is None:
        return None
    return get_key_provider().decrypt(material.ciphertext, material.key_ref)


async def secret_named(session: AsyncSession, name: str) -> bytes | None:
    material = await repo.by_name(session, name)
    if material is None:
        return None
    return get_key_provider().decrypt(material.ciphertext, material.key_ref)


async def delete(
    session: AsyncSession, principal: AccessClaims, credential_id: uuid.UUID
) -> None:
    if await repo.get(session, credential_id) is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such credential.")

    in_use = await repo.referencing_repos(session, credential_id)
    if in_use:
        raise PlimsollError(
            ErrorCode.RESOURCE_IN_USE,
            "This credential is still used by a script repository.",
            {"scriptRepos": in_use},
        )

    await repo.delete(session, credential_id)
    await audit.record(
        session,
        principal=principal,
        action="credential.deleted",
        entity_type="credential",
        entity_id=credential_id,
    )
```

- [ ] **Step 7: Write the router**

`apps/api/plimsoll_api/routers/credentials.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import credentials as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import credentials as service
from plimsoll_contracts.credentials import CredentialCreate, CredentialResponse
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    Page,
    decode_cursor,
    encode_cursor,
)

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


def _response(row: Any) -> CredentialResponse:
    return CredentialResponse(id=row.id, name=row.name, kind=row.kind, created_at=row.created_at)


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def create_credential(
    body: CredentialCreate, principal: CurrentPrincipal, session: TenantSession
) -> CredentialResponse:
    return _response(await service.create(session, principal, body))


@router.get(
    "",
    response_model=Page[CredentialResponse],
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def list_credentials(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[CredentialResponse]:
    after = None
    if cursor is not None:
        try:
            payload = decode_cursor(cursor)
            after = (datetime.fromisoformat(payload["createdAt"]), uuid.UUID(payload["id"]))
        except (ValueError, KeyError) as exc:
            raise PlimsollError(ErrorCode.VALIDATION_FAILED, "The cursor is malformed.") from exc

    rows = await repo.list_page(session, limit=limit + 1, after=after)
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(
            {"createdAt": rows[-1].created_at.isoformat(), "id": str(rows[-1].id)}
        )
    return Page(items=[_response(row) for row in rows], next_cursor=next_cursor)


@router.delete(
    "/{credential_id}",
    status_code=204,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def delete_credential(
    credential_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.delete(session, principal, credential_id)
    return Response(status_code=204)
```

There is deliberately no `GET /credentials/{id}`: a per-credential read invites a "reveal" parameter. FastAPI answers the path with `405`, which the test asserts.

Register it in `main.py`.

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_credentials_api.py -v -m integration`
Expected: PASS — seven tests.

- [ ] **Step 9: Regenerate contracts and commit**

```bash
make contracts
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): credentials, encrypted on the way in and never returned"
```

---

### Task 6: Generator pools

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/pools.py`, `apps/api/plimsoll_api/repositories/pools.py`, `apps/api/plimsoll_api/services/pools.py`, `apps/api/plimsoll_api/routers/pools.py`
- Modify: `apps/api/plimsoll_api/main.py`, `apps/api/plimsoll_api/seed.py`
- Test: `apps/api/tests/integration/test_pools_api.py`

**Interfaces:**
- Produces: contracts `PoolCreate`, `PoolUpdate`, `PoolResponse`; `pools.capacity_for(session, pool_id) -> int` returning `max_generators * max_vus_per_generator`, consumed by preflight in S2b; a seeded `local-docker` pool.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_pools_api.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _body(name: str) -> dict[str, object]:
    return {
        "name": name,
        "runtime": "docker",
        "config": {"image": "ghcr.io/ultron13/generator:jmeter-5.6.3"},
        "maxGenerators": 4,
        "maxVusPerGenerator": 500,
    }


def _name() -> str:
    return f"pool-{uuid.uuid4().hex[:8]}"


def test_a_pool_is_created_with_its_capacity(admin_client: httpx.Client) -> None:
    response = admin_client.post("/api/v1/generator-pools", json=_body(_name()))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["maxGenerators"] == 4
    assert body["capacity"] == 2000
    assert body["supportedEngines"] == ["jmeter"]


def test_the_seeded_pool_is_present(admin_client: httpx.Client) -> None:
    names = [item["name"] for item in admin_client.get("/api/v1/generator-pools").json()["items"]]
    assert "local-docker" in names


def test_an_unknown_runtime_is_rejected(admin_client: httpx.Client) -> None:
    body = _body(_name()) | {"runtime": "mainframe"}
    response = admin_client.post("/api/v1/generator-pools", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_zero_capacity_is_rejected(admin_client: httpx.Client) -> None:
    body = _body(_name()) | {"maxVusPerGenerator": 0}
    assert admin_client.post("/api/v1/generator-pools", json=body).status_code == 422


def test_a_pool_is_updated_and_archived(admin_client: httpx.Client) -> None:
    created = admin_client.post("/api/v1/generator-pools", json=_body(_name())).json()
    updated = admin_client.patch(
        f"/api/v1/generator-pools/{created['id']}", json={"maxGenerators": 8}
    )
    assert updated.status_code == 200
    assert updated.json()["capacity"] == 4000
    assert admin_client.delete(f"/api/v1/generator-pools/{created['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/generator-pools/{created['id']}").json()["status"] == "ARCHIVED"


def test_a_viewer_cannot_create_one(viewer_client: httpx.Client) -> None:
    assert viewer_client.post("/api/v1/generator-pools", json=_body(_name())).status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_pools_api.py -v -m integration`
Expected: FAIL — `404` from `POST /api/v1/generator-pools`.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/pools.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PoolRuntime(StrEnum):
    DOCKER = "docker"
    KUBERNETES = "kubernetes"


class PoolCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    runtime: PoolRuntime
    config: dict[str, Any] = Field(default_factory=dict)
    region: str | None = Field(default=None, max_length=100)
    max_generators: int = Field(ge=1, alias="maxGenerators")
    max_vus_per_generator: int = Field(ge=1, alias="maxVusPerGenerator")


class PoolUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    region: str | None = Field(default=None, max_length=100)
    max_generators: int | None = Field(default=None, ge=1, alias="maxGenerators")
    max_vus_per_generator: int | None = Field(default=None, ge=1, alias="maxVusPerGenerator")


class PoolResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    runtime: str
    config: dict[str, Any]
    region: str | None
    max_generators: int = Field(serialization_alias="maxGenerators")
    max_vus_per_generator: int = Field(serialization_alias="maxVusPerGenerator")
    # Stated rather than left to the client to multiply, because preflight and
    # the UI must agree on what a pool can supply.
    capacity: int
    supported_engines: list[str] = Field(serialization_alias="supportedEngines")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")
```

- [ ] **Step 4: Write the repository, service, and router**

`apps/api/plimsoll_api/repositories/pools.py` takes the same shape as `repositories/projects.py` — `get`, `list_page` with the identical keyset clause on `(created_at, id)`, `update` from a fixed column set, and `archive` setting `status = 'ARCHIVED'` — over:

```python
COLUMNS = (
    "id, name, runtime, config, region, max_generators, max_vus_per_generator, "
    "supported_engines, status, created_at"
)
```

`insert` differs only in binding JSON:

```python
async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    runtime: str,
    config: dict[str, Any],
    region: str | None,
    max_generators: int,
    max_vus_per_generator: int,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO generator_pools "
                "(id, organization_id, name, runtime, config, region, max_generators, "
                " max_vus_per_generator) "
                "VALUES (:id, :org, :name, :runtime, CAST(:config AS jsonb), :region, "
                ":max_generators, :max_vus_per_generator) "
                f"RETURNING {COLUMNS}"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "runtime": runtime,
                "config": json.dumps(config),
                "region": region,
                "max_generators": max_generators,
                "max_vus_per_generator": max_vus_per_generator,
            },
        )
    ).one()
```

`update` must serialise `config` the same way when it appears in `changes`; a dict bound straight into a `jsonb` column raises `ProgrammingError`.

Add the two queries preflight needs in S2b:

```python
async def capacity(session: AsyncSession, pool_id: uuid.UUID) -> int | None:
    value: int | None = await session.scalar(
        sa.text(
            "SELECT max_generators * max_vus_per_generator FROM generator_pools "
            "WHERE id = :id AND status = 'ACTIVE'"
        ),
        {"id": pool_id},
    )
    return value


async def committed_users(session: AsyncSession, pool_id: uuid.UUID) -> int:
    """Virtual users held by generators of runs that have not finished.

    Derived rather than counted on the pool: a mutable counter updated outside
    a transaction boundary drifts, and this is the same data the orchestrator
    acts on. Returns zero until S3 creates runs.
    """
    value: int | None = await session.scalar(
        sa.text(
            "SELECT COALESCE(SUM(rg.assigned_users), 0) FROM run_generators rg "
            "JOIN test_runs r ON r.id = rg.run_id "
            "WHERE rg.pool_id = :id AND r.status IN ('QUEUED', 'STARTING', 'RUNNING', 'STOPPING')"
        ),
        {"id": pool_id},
    )
    return value or 0
```

`services/pools.py` mirrors `services/projects.py`: `create` (audit `pool.created`, `IntegrityError` on the `(organization_id, name)` unique constraint → `CONFLICT`), `require` (404), `update` with `UPDATABLE = {"name", "config", "region", "max_generators", "max_vus_per_generator"}` (audit `pool.updated`), `archive` (audit `pool.deleted`), and:

```python
async def capacity_for(session: AsyncSession, pool_id: uuid.UUID) -> int:
    """Free capacity: what the pool can supply, less what active runs hold."""
    total = await repo.capacity(session, pool_id)
    if total is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such generator pool.")
    return total - await repo.committed_users(session, pool_id)
```

`routers/pools.py` mirrors `routers/projects.py` with prefix `/api/v1/generator-pools`, tag `pools`, `Permission.ADMIN_SYSTEM` on writes and `Permission.PROJECT_READ` on reads, and a `_response` that computes `capacity=row.max_generators * row.max_vus_per_generator`. Register it in `main.py`.

`POST /generator-pools/{id}/test-connection` is **not** part of this task: it needs the runtime S3 builds.

- [ ] **Step 5: Seed a pool**

In `apps/api/plimsoll_api/seed.py`, after the project insert:

```python
        await connection.execute(
            sa.text(
                "INSERT INTO generator_pools "
                "(id, organization_id, name, runtime, config, max_generators, "
                " max_vus_per_generator) "
                "VALUES (:id, :org, 'local-docker', 'docker', CAST(:config AS jsonb), 2, 500) "
                "ON CONFLICT (organization_id, name) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "org": DEMO_ORG_ID,
                "config": DEMO_POOL_CONFIG,
            },
        )
```

with, near the other constants:

```python
# Two generators of 500 users each: enough for the demo test to pass preflight,
# small enough to run on a laptop. S3 gives the runtime meaning.
DEMO_POOL_CONFIG = '{"image": "ghcr.io/ultron13/generator:jmeter-5.6.3"}'
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_pools_api.py apps/api/tests/integration/test_seed.py -v -m integration`
Expected: PASS — six pool tests, and the seed suite still green including its idempotence test.

- [ ] **Step 7: Regenerate contracts and commit**

```bash
make contracts
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): generator pool configuration with derived capacity"
```

---

### Task 7: The target policy

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/policy.py`, `apps/api/plimsoll_api/repositories/target_policy.py`, `apps/api/plimsoll_api/services/target_policy.py`, `apps/api/plimsoll_api/routers/target_policy.py`
- Modify: `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/unit/test_target_policy_rules.py`, `apps/api/tests/integration/test_target_policy_api.py`

**Interfaces:**
- Produces: `validate_entries(entries: list[str]) -> None` (raises `PlimsollError`), `matches_allowlist(host: str, entries: list[str]) -> bool`, `current(session) -> sa.Row | None`, contracts `TargetPolicyUpdate`, `TargetPolicyResponse`. Preflight in S2b consumes `matches_allowlist` and `current`.

- [ ] **Step 1: Write the failing unit test**

`apps/api/tests/unit/test_target_policy_rules.py`:

```python
import pytest

from plimsoll_api.errors import PlimsollError
from plimsoll_api.services.target_policy import matches_allowlist, validate_entries

ALLOWLIST = ["demo-target", ".example.com", "10.0.0.0/8"]


@pytest.mark.parametrize(
    "host",
    ["demo-target", "api.example.com", "deep.api.example.com", "10.4.2.1"],
)
def test_a_permitted_host_matches(host: str) -> None:
    assert matches_allowlist(host, ALLOWLIST)


@pytest.mark.parametrize(
    "host",
    ["demo-target.evil.com", "example.com.evil.net", "notexample.com", "11.0.0.1", "127.0.0.1"],
)
def test_an_unpermitted_host_does_not_match(host: str) -> None:
    assert not matches_allowlist(host, ALLOWLIST)


def test_the_bare_suffix_domain_matches_itself() -> None:
    assert matches_allowlist("example.com", [".example.com"])


def test_an_empty_allowlist_permits_nothing() -> None:
    assert not matches_allowlist("demo-target", [])


def test_matching_is_case_insensitive() -> None:
    assert matches_allowlist("API.Example.COM", ALLOWLIST)


@pytest.mark.parametrize(
    "entry",
    ["127.0.0.1", "localhost", "169.254.169.254", "169.254.0.0/16", "::1", "0.0.0.0/0"],
)
def test_a_dangerous_entry_is_refused(entry: str) -> None:
    with pytest.raises(PlimsollError):
        validate_entries([entry])


@pytest.mark.parametrize("entry", ["", "  ", "http://example.com", "example.com/path"])
def test_a_malformed_entry_is_refused(entry: str) -> None:
    with pytest.raises(PlimsollError):
        validate_entries([entry])


def test_a_reasonable_allowlist_is_accepted() -> None:
    validate_entries(ALLOWLIST)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_target_policy_rules.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.services.target_policy`.

- [ ] **Step 3: Write the rules**

`apps/api/plimsoll_api/services/target_policy.py` (rules first; the persistence functions follow in Step 5):

```python
"""The control that separates a load test from an attack.

An empty allowlist permits nothing. There is no implicit permit-all state and
no first-run convenience exception -- ADR-0007 rejected warn-and-audit, and a
policy that starts open is the same thing by another name.
"""

from __future__ import annotations

import ipaddress
import re

from plimsoll_api.errors import PlimsollError
from plimsoll_contracts.errors import ErrorCode

HOSTNAME = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")

# Blocked from generator containers regardless of allowlist, so an entry that
# would permit one is refused at write time rather than surprising a run.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]
BLOCKED_NAMES = {"localhost", "metadata.google.internal"}


def _refuse(entry: str, reason: str) -> None:
    raise PlimsollError(
        ErrorCode.VALIDATION_FAILED,
        f"Allowlist entry {entry!r} is not permitted: {reason}",
        {"entry": entry},
    )


def validate_entries(entries: list[str]) -> None:
    for entry in entries:
        candidate = entry.strip().lower()
        if not candidate or candidate != entry.lower():
            _refuse(entry, "an entry must not be empty or padded with whitespace.")
        if "/" in candidate and not _is_cidr(candidate):
            _refuse(entry, "it looks like a URL; use a hostname, suffix, or CIDR.")

        if _is_cidr(candidate):
            network = ipaddress.ip_network(candidate, strict=False)
            if network.prefixlen == 0:
                _refuse(entry, "it permits every address.")
            if any(network.overlaps(blocked) for blocked in BLOCKED_NETWORKS):
                _refuse(entry, "it overlaps loopback or link-local addresses.")
            continue

        bare = candidate.removeprefix(".")
        if bare in BLOCKED_NAMES:
            _refuse(entry, "loopback and metadata hosts are never reachable from a generator.")
        if _is_address(bare):
            address = ipaddress.ip_address(bare)
            if any(address in blocked for blocked in BLOCKED_NETWORKS):
                _refuse(entry, "loopback and link-local addresses are never permitted.")
            continue
        if not HOSTNAME.match(bare):
            _refuse(entry, "it is neither a hostname, a domain suffix, nor a CIDR.")


def matches_allowlist(host: str, entries: list[str]) -> bool:
    """Matched on the host as written in the plan.

    No DNS resolution here: resolving at admission would open exactly the
    repoint window the agent's second check exists to close.
    """
    candidate = host.strip().lower()
    if not candidate:
        return False

    for entry in entries:
        rule = entry.strip().lower()
        if _is_cidr(rule):
            if _is_address(candidate) and ipaddress.ip_address(candidate) in ipaddress.ip_network(
                rule, strict=False
            ):
                return True
        elif rule.startswith("."):
            suffix = rule.removeprefix(".")
            if candidate == suffix or candidate.endswith(f".{suffix}"):
                return True
        elif candidate == rule:
            return True
    return False


def _is_cidr(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return "/" in value


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
```

- [ ] **Step 4: Run the unit tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_target_policy_rules.py -v`
Expected: PASS — all parametrised cases.

- [ ] **Step 5: Write the failing integration test**

`apps/api/tests/integration/test_target_policy_api.py`:

```python
import httpx
import pytest

pytestmark = pytest.mark.integration


def test_the_seeded_policy_allows_only_the_demo_target(admin_client: httpx.Client) -> None:
    body = admin_client.get("/api/v1/target-policy").json()
    assert body["allowlist"] == ["demo-target"]
    assert body["version"] >= 1


def test_a_put_creates_a_new_version(admin_client: httpx.Client) -> None:
    before = admin_client.get("/api/v1/target-policy").json()
    response = admin_client.put(
        "/api/v1/target-policy", json={"allowlist": ["demo-target", ".example.com"]}
    )
    assert response.status_code == 200
    assert response.json()["version"] == before["version"] + 1
    assert response.json()["allowlist"] == ["demo-target", ".example.com"]

    # Restore, so the rest of the suite sees the seeded policy.
    admin_client.put("/api/v1/target-policy", json={"allowlist": ["demo-target"]})


def test_a_dangerous_entry_is_refused(admin_client: httpx.Client) -> None:
    response = admin_client.put(
        "/api/v1/target-policy", json={"allowlist": ["demo-target", "169.254.169.254"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert admin_client.get("/api/v1/target-policy").json()["allowlist"] == ["demo-target"]


def test_an_empty_allowlist_is_accepted_and_permits_nothing(admin_client: httpx.Client) -> None:
    assert admin_client.put("/api/v1/target-policy", json={"allowlist": []}).status_code == 200
    assert admin_client.get("/api/v1/target-policy").json()["allowlist"] == []
    admin_client.put("/api/v1/target-policy", json={"allowlist": ["demo-target"]})


def test_a_viewer_may_read_but_not_write(viewer_client: httpx.Client) -> None:
    assert viewer_client.get("/api/v1/target-policy").status_code == 200
    assert viewer_client.put("/api/v1/target-policy", json={"allowlist": []}).status_code == 403
```

Run it: FAIL with `404` from `GET /api/v1/target-policy`.

- [ ] **Step 6: Write the contracts, repository, and router**

`packages/contracts/python/plimsoll_contracts/policy.py`:

```python
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TargetPolicyUpdate(BaseModel):
    # An empty list is valid and permits no runs. That is the safe state, not a
    # configuration error.
    allowlist: list[str]


class TargetPolicyResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    version: int
    allowlist: list[str]
    created_at: datetime = Field(serialization_alias="createdAt")
```

`apps/api/plimsoll_api/repositories/target_policy.py`:

```python
from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def current(session: AsyncSession) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT version, allowlist, created_at FROM target_policies "
                "ORDER BY version DESC LIMIT 1"
            )
        )
    ).first()


async def insert_next_version(
    session: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID, allowlist: list[str]
) -> sa.Row[Any]:
    """Rows are immutable: a change is a new version, so a historical run can
    resolve what was permitted when it ran."""
    return (
        await session.execute(
            sa.text(
                "INSERT INTO target_policies (id, organization_id, version, allowlist, created_by) "
                "SELECT :id, :org, COALESCE(MAX(version), 0) + 1, CAST(:allowlist AS jsonb), :by "
                "FROM target_policies "
                "RETURNING version, allowlist, created_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "allowlist": json.dumps(allowlist),
                "by": created_by,
            },
        )
    ).one()
```

Add to `services/target_policy.py`:

```python
async def current_policy(session: AsyncSession) -> sa.Row[Any] | None:
    return await repo.current(session)


async def replace(
    session: AsyncSession, principal: AccessClaims, allowlist: list[str]
) -> sa.Row[Any]:
    validate_entries(allowlist)
    row = await repo.insert_next_version(
        session,
        org_id=principal.organization_id,
        created_by=principal.user_id,
        allowlist=allowlist,
    )
    await audit.record(
        session,
        principal=principal,
        action="target_policy.updated",
        entity_type="target_policy",
        metadata={"version": row.version, "entries": len(allowlist)},
    )
    return row
```

`apps/api/plimsoll_api/routers/target_policy.py` exposes `GET` (permission `PROJECT_READ`) returning the current version — or version `0` with an empty allowlist when none exists, because "no policy" and "empty policy" permit exactly the same thing — and `PUT` (permission `ADMIN_SYSTEM`) returning the new version. Prefix `/api/v1/target-policy`, tag `policy`. Register it in `main.py`.

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/unit/test_target_policy_rules.py apps/api/tests/integration/test_target_policy_api.py -v`
Expected: PASS. The `RETURNING` on the `INSERT … SELECT` yields the allowlist as JSON; if the response arrives as a string rather than a list, cast it in the router with `json.loads` — do not change the column type.

- [ ] **Step 8: Regenerate contracts and commit**

```bash
make contracts
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): versioned target policy that rejects by default"
```

---

### Task 8: The authorisation sweep

**Files:**
- Test: `apps/api/tests/integration/test_write_authorisation.py`

**Interfaces:**
- Consumes: the FastAPI app object and every router registered so far.
- Produces: a test that fails when an endpoint is added without a permission guard. S2b extends nothing — it inherits this automatically.

- [ ] **Step 1: Write the test**

`apps/api/tests/integration/test_write_authorisation.py`:

```python
"""Every state-changing route is guarded, enumerated from the router itself.

Listing the endpoints by hand would pass forever the moment someone adds one
and forgets the guard, which is the failure this exists to catch.
"""

import httpx
import pytest
from fastapi.routing import APIRoute

from plimsoll_api.main import create_app
from plimsoll_api.security.permissions import requires

pytestmark = pytest.mark.integration

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
# Login, refresh, and logout establish a principal and therefore cannot require
# one. They are authenticated by the credential in the request itself.
UNGUARDED_BY_DESIGN = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
}


def _write_routes() -> list[tuple[str, APIRoute]]:
    routes = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods & WRITE_METHODS):
            routes.append((method, route))
    return routes


def test_every_write_route_declares_a_permission() -> None:
    unguarded = [
        f"{method} {route.path}"
        for method, route in _write_routes()
        if (method, route.path) not in UNGUARDED_BY_DESIGN
        and not any(
            getattr(dependency.call, "__qualname__", "").startswith(requires.__name__)
            for dependency in route.dependant.dependencies
        )
    ]
    assert unguarded == [], f"write routes without a permission guard: {unguarded}"


def test_a_viewer_is_refused_by_every_guarded_write(viewer_client: httpx.Client) -> None:
    refused = []
    for method, route in _write_routes():
        if (method, route.path) in UNGUARDED_BY_DESIGN:
            continue
        # Path parameters get a syntactically valid value; authorisation is
        # checked before the handler runs, so the target need not exist.
        path = route.path
        for parameter in route.dependant.path_params:
            path = path.replace(
                f"{{{parameter.name}}}", "00000000-0000-0000-0000-000000000000"
            )
        response = viewer_client.request(method, path, json={})
        if response.status_code != 403:
            refused.append(f"{method} {path} -> {response.status_code}")
    assert refused == [], f"writes a VIEWER was not refused: {refused}"
```

- [ ] **Step 2: Run it**

Run: `make dev && uv run pytest apps/api/tests/integration/test_write_authorisation.py -v -m integration`
Expected: PASS. If the guard-detection assertion fails, print `route.dependant.dependencies` for one known-good route and match on how `requires` closures actually appear — the enumeration must reflect reality, not the other way round. Do **not** weaken the test to a hard-coded list.

- [ ] **Step 3: Commit**

```bash
git add apps/api/tests/integration/test_write_authorisation.py
git commit -s -m "test(api): every write route is enumerated and guarded"
```

---

## Plan acceptance

```bash
make dev-down && make dev
make lint && make typecheck && make test && make test-int
make contracts && git diff --quiet
```

- [ ] A project, credential, pool, and target-policy version can each be created over HTTP by the seeded admin
- [ ] A `VIEWER` is refused every write and permitted every read
- [ ] No response body contains a stored secret
- [ ] Deleting a credential a script repository references returns `RESOURCE_IN_USE`
- [ ] `PUT /target-policy` creates a new version and refuses a loopback, link-local, or metadata entry
- [ ] Every state change has an `audit_logs` row, and a rolled-back change has none
- [ ] `make contracts` leaves the tree clean
