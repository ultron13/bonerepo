# Plimsoll v0.1 Slice 3a — Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A test defined in S2 starts a run over HTTP, real containers are created for it, their agents register and are coordinated through a full lifecycle, and the run stops cleanly and idempotently.

**Architecture:** The API accepts a run, writes it with an immutable snapshot, and publishes to a Redis stream. A new worker process consumes that stream and **reconciles**: it loads the run and its generators, compares desired state to actual, and takes the next action — so a duplicate delivery, a restart, or a lost race all converge instead of compounding. Agents connect outbound over WebSocket to the API, and commands reach them through Postgres (the truth) announced over Redis pub/sub (the nudge). No JMeter in this plan: the agent waits out its duration and reports terminal state.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 Core, Pydantic v2, `redis.asyncio`, the `docker` SDK, `websockets`, pytest.

Design: [`docs/superpowers/specs/2026-08-13-v01-s3-execution-design.md`](../specs/2026-08-13-v01-s3-execution-design.md).
Prerequisite: S2 — projects, credentials, pools, target policy, script repos, tests, preflight.

## Global Constraints

- Everything in S2's Global Constraints continues to apply — no migration, `sa.text()` with bind parameters, `TenantSession`, a permission guard and an audit row on every write, `make contracts` before any commit that changes the API surface.
- **A database transaction is never held open across a network call.** This now includes the Redis publish: run creation commits, then publishes.
- **Every run-status and generator-status change is a conditional update** — `UPDATE … WHERE status = :expected`. A transition that loses the race did not happen, and the caller must handle that rather than assume success.
- **The agent never imports `plimsoll_api`.** Its dependencies are `plimsoll-contracts`, `websockets`, and `httpx`. A review that finds `from plimsoll_api…` in `apps/agent` rejects the change.
- **Generators are created with restart policy `no`** ([invariant 6](../../../CLAUDE.md)). A restarted generator resets virtual-user state mid-test and silently corrupts the run.
- Every query is organisation-scoped. The worker binds each session with `session_for_org`, using the organisation id carried in the message ([invariant 4](../../../CLAUDE.md)).

## File Structure

```
apps/api/plimsoll_api/
  messaging.py                     MessageBus, Delivery, RedisStreamBus, announce/listen
  allocation.py                    Pure: users across generators
  repositories/runs.py             test_runs and run_generators
  services/runs.py                 Creation, snapshot, transitions, stop/cancel
  services/preflight.py            (modified) gather() and assess() extracted
  routers/runs.py                  The run API
  routers/agent.py                 WS /api/v1/agent/runs/{runId}
  security/tokens.py               (modified) agent tokens
  security/permissions.py          (modified) TEST_EXECUTE
apps/worker/
  pyproject.toml
  plimsoll_worker/__init__.py __main__.py
  plimsoll_worker/reconciler.py    Pure decision + impure application
  plimsoll_worker/runtime/base.py  GeneratorRuntime, ExecutionPlan, GeneratorHandle
  plimsoll_worker/runtime/docker.py DockerRuntime
apps/agent/
  pyproject.toml
  plimsoll_agent/__init__.py __main__.py
  plimsoll_agent/channel.py        Connect, register, heartbeat, obey
  plimsoll_agent/lifecycle.py      The agent state machine
packages/contracts/python/plimsoll_contracts/
  runs.py                          RunStatus, GeneratorStatus, RunResponse, RunStatusResponse
  agent.py                         The wire protocol, shared by both ends
infrastructure/docker/
  generator.Dockerfile
apps/api/tests/integration/
  test_messaging.py test_runs_api.py test_agent_channel.py
  test_run_execution.py test_run_failure.py
apps/api/tests/unit/
  test_allocation.py
apps/worker/tests/unit/
  test_reconciler.py
apps/worker/tests/integration/
  test_docker_runtime.py
```

---

### Task 1: The message bus

**Files:**
- Create: `apps/api/plimsoll_api/messaging.py`
- Test: `apps/api/tests/integration/test_messaging.py`

**Interfaces:**
- Produces: `Delivery(id, stream, payload)`, `MessageBus` protocol, `RedisStreamBus`, `get_bus()`, `RUNS_EXECUTION = "runs.execution"`, `announce(channel, payload)`, `listen(channel)`.

- [x] **Step 1: Write the failing test**

`apps/api/tests/integration/test_messaging.py`:

```python
"""Redis Streams, exercised against the real broker.

At-least-once delivery is the property the worker is built on, so the test that
matters most here is that an unacknowledged message comes back.
"""

import uuid
from datetime import timedelta

import pytest

from plimsoll_api.messaging import RedisStreamBus

pytestmark = pytest.mark.integration

GROUP = "test-group"


@pytest.fixture
def stream() -> str:
    return f"test.stream.{uuid.uuid4().hex[:8]}"


async def test_a_published_message_is_read_by_the_group(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})

    deliveries = await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=1000)
    assert [delivery.payload["runId"] for delivery in deliveries] == ["abc"]


async def test_an_acknowledged_message_is_not_reclaimed(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})

    delivered = await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=1000)
    await bus.acknowledge(stream, GROUP, delivered[0])

    reclaimed = await bus.reclaim_stale(
        stream, GROUP, "consumer-2", idle=timedelta(seconds=0)
    )
    assert reclaimed == []


async def test_an_unacknowledged_message_is_reclaimed(stream: str) -> None:
    """A worker that dies holding a message must not take the run with it."""
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.publish(stream, {"runId": "abc"})
    await bus.read(stream, GROUP, "dying-consumer", count=10, block_ms=1000)

    reclaimed = await bus.reclaim_stale(
        stream, GROUP, "surviving-consumer", idle=timedelta(seconds=0)
    )
    assert [delivery.payload["runId"] for delivery in reclaimed] == ["abc"]


async def test_reading_an_empty_stream_returns_nothing(stream: str) -> None:
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    assert await bus.read(stream, GROUP, "consumer-1", count=10, block_ms=50) == []


async def test_creating_a_group_twice_is_not_an_error(stream: str) -> None:
    """Every worker calls this at startup; only one of them is first."""
    bus = RedisStreamBus()
    await bus.ensure_group(stream, GROUP)
    await bus.ensure_group(stream, GROUP)


async def test_an_announcement_reaches_a_listener() -> None:
    channel = f"test.channel.{uuid.uuid4().hex[:8]}"
    bus = RedisStreamBus()
    received: list[dict[str, str]] = []

    async with bus.listen(channel) as messages:
        await bus.announce(channel, {"command": "stop"})
        async for message in messages:
            received.append(message)
            break

    assert received == [{"command": "stop"}]
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_messaging.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.messaging`.

- [x] **Step 3: Write the bus**

`apps/api/plimsoll_api/messaging.py`:

```python
"""Durable queueing and live announcements, both on Redis.

ADR-0005 chose Redis Streams for v0.1 and put a seam in front of it so RabbitMQ
or Kafka is a configuration change rather than a refactor. Everything the
platform enqueues goes through `MessageBus`; nothing calls redis directly.

Delivery is at-least-once. That is not a caveat to work around -- it is the
contract consumers are written against.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Protocol

import redis.asyncio as redis
from redis.exceptions import ResponseError

from plimsoll_api.config import get_settings

RUNS_EXECUTION = "runs.execution"
WORKER_GROUP = "orchestrators"


def run_channel(run_id: Any) -> str:
    """Commands for one run's agents. Namespaced so a listener cannot subscribe
    to another run by guessing a shorter key."""
    return f"runs:{run_id}:commands"


@dataclass(frozen=True)
class Delivery:
    id: str
    stream: str
    payload: dict[str, str]


class MessageBus(Protocol):
    async def publish(self, stream: str, payload: dict[str, str]) -> str: ...

    async def ensure_group(self, stream: str, group: str) -> None: ...

    async def read(
        self, stream: str, group: str, consumer: str, *, count: int, block_ms: int
    ) -> list[Delivery]: ...

    async def acknowledge(self, stream: str, group: str, delivery: Delivery) -> None: ...

    async def reclaim_stale(
        self, stream: str, group: str, consumer: str, *, idle: timedelta
    ) -> list[Delivery]: ...

    async def announce(self, channel: str, payload: dict[str, str]) -> None: ...


class RedisStreamBus:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.from_url(url or get_settings().redis_url, decode_responses=True)

    async def publish(self, stream: str, payload: dict[str, str]) -> str:
        message_id: str = await self._client.xadd(stream, payload)
        return message_id

    async def ensure_group(self, stream: str, group: str) -> None:
        try:
            # mkstream so a consumer may start before the first producer.
            await self._client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def read(
        self, stream: str, group: str, consumer: str, *, count: int, block_ms: int
    ) -> list[Delivery]:
        response = await self._client.xreadgroup(
            group, consumer, {stream: ">"}, count=count, block=block_ms
        )
        return [
            Delivery(id=message_id, stream=stream_name, payload=payload)
            for stream_name, messages in response or []
            for message_id, payload in messages
        ]

    async def acknowledge(self, stream: str, group: str, delivery: Delivery) -> None:
        await self._client.xack(stream, group, delivery.id)

    async def reclaim_stale(
        self, stream: str, group: str, consumer: str, *, idle: timedelta
    ) -> list[Delivery]:
        """Take over messages a dead consumer is still holding."""
        _, messages, _ = await self._client.xautoclaim(
            stream, group, consumer, int(idle.total_seconds() * 1000), start_id="0"
        )
        return [
            Delivery(id=message_id, stream=stream, payload=payload)
            for message_id, payload in messages
        ]

    async def announce(self, channel: str, payload: dict[str, str]) -> None:
        await self._client.publish(channel, json.dumps(payload))

    @asynccontextmanager
    async def listen(self, channel: str) -> AsyncIterator[AsyncIterator[dict[str, str]]]:
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)

        async def messages() -> AsyncIterator[dict[str, str]]:
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    decoded: dict[str, str] = json.loads(raw["data"])
                    yield decoded

        try:
            yield messages()
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


_bus: RedisStreamBus | None = None


def get_bus() -> RedisStreamBus:
    """One client per process; redis-py pools its connections internally."""
    global _bus
    if _bus is None:
        _bus = RedisStreamBus()
    return _bus
```

- [x] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_messaging.py -v -m integration`
Expected: PASS — six tests. If `xautoclaim` rejects `start_id`, the installed redis-py names it positionally; check `redis.asyncio.Redis.xautoclaim` and pass it as the argument after the idle time.

- [x] **Step 5: Commit**

```bash
git add apps/api/plimsoll_api/messaging.py apps/api/tests/integration/test_messaging.py
git commit -s -m "feat(worker): a message bus seam over Redis Streams"
```

---

### Task 2: Preflight, reusable

**Files:**
- Modify: `apps/api/plimsoll_api/services/preflight.py`, `apps/api/plimsoll_api/routers/performance_tests.py`
- Test: `apps/api/tests/integration/test_preflight.py` (extended)

**Interfaces:**
- Produces: `preflight.gather(session, test_id) -> PreflightInput`; `preflight.assess(inputs) -> Assessment(report, resolved)`; `PlanInput` gains `script_repo_id`. `preflight.run(inputs)` keeps returning `PreflightReport` so nothing else changes.

Run creation needs two things `validate` already computes: the inputs, and the commit SHAs preflight resolved. Duplicating either would let the snapshot and the check disagree, which is the one thing [invariant 3](../../../CLAUDE.md) forbids.

- [x] **Step 1: Write the failing test**

Append to `apps/api/tests/integration/test_preflight.py`:

```python
async def test_assess_returns_the_commit_it_resolved(admin_org: uuid.UUID) -> None:
    """The snapshot is built from these, so they must be the real SHAs and not
    the truncated ones the report prints."""
    from plimsoll_api.db.session import session_for_org
    from plimsoll_api.services import preflight

    async with session_for_org(admin_org) as session:
        inputs = await preflight.gather(session, DEMO_TEST_ID)

    assessment = await preflight.assess(inputs)
    assert assessment.report.ok is True
    assert list(assessment.resolved) == [0]
    assert len(assessment.resolved[0]) == 40


async def test_gather_names_the_repository_behind_each_plan(admin_org: uuid.UUID) -> None:
    from plimsoll_api.db.session import session_for_org
    from plimsoll_api.services import preflight

    async with session_for_org(admin_org) as session:
        inputs = await preflight.gather(session, DEMO_TEST_ID)

    assert inputs.plans[0].script_repo_id is not None
    assert inputs.plans[0].plan_path == "perf/checkout.jmx"
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_preflight.py -v -m integration -k "assess or gather"`
Expected: FAIL — `AttributeError: module 'plimsoll_api.services.preflight' has no attribute 'gather'`.

- [x] **Step 3: Move `_preflight_input` into the service**

Cut `_preflight_input` out of `apps/api/plimsoll_api/routers/performance_tests.py` and paste it into `apps/api/plimsoll_api/services/preflight.py` as `gather`, taking `AsyncSession` instead of `TenantSession`:

```python
async def gather(session: AsyncSession, test_id: uuid.UUID) -> PreflightInput:
    """Everything the checks need, read in one transaction and nothing more.

    The Git work runs after this returns, outside the transaction: a clone held
    open against a stranger's host would hold a connection with it.
    """
    document = await performance_tests.require(session, test_id)
    configuration = WorkloadSpec.model_validate(document.row.configuration)

    plans = []
    for plan in document.plans:
        row = await script_repos.require(session, plan.script_repo_id)
        plans.append(
            PlanInput(
                script_repo_id=plan.script_repo_id,
                repo_name=row.name,
                access=await script_repos.access_for(session, row),
                plan_path=row.plan_path,
                ref=plan.pinned_ref or row.default_ref,
                virtual_users=plan.virtual_users,
            )
        )

    policy = await target_policy.current_policy(session)
    try:
        free_capacity: int | None = await pools.capacity_for(
            session, configuration.generator_pool_id
        )
    except PlimsollError:
        # An archived or deleted pool is a capacity failure to report, not a
        # reason to refuse the caller an answer about everything else.
        free_capacity = None

    return PreflightInput(
        requested_users=configuration.virtual_users,
        plans=plans,
        allowlist=list(policy.allowlist) if policy is not None else [],
        variables=await credentials.variables(session),
        free_capacity=free_capacity,
    )
```

Add `script_repo_id: uuid.UUID` as the first field of `PlanInput`, and import what the moved function needs: `AsyncSession`, `PlimsollError`, `WorkloadSpec`, and the `credentials`, `performance_tests`, `pools`, `script_repos`, `target_policy` services. In `routers/performance_tests.py`, the `validate` endpoint now calls `preflight.gather(session, test_id)`, and the now-unused imports come out.

- [x] **Step 4: Return the resolutions**

In `services/preflight.py`, keep the check logic in one place and let the caller see what it resolved:

```python
@dataclass(frozen=True)
class Assessment:
    """The report, and the commits behind it.

    A run's snapshot is built from `resolved`, so the commit that was checked
    and the commit that will execute are the same object rather than two
    lookups that agree today.
    """

    report: PreflightReport
    # Index into PreflightInput.plans -> resolved commit SHA. Indexed rather
    # than named: two repositories in one organisation may share a name.
    resolved: dict[int, str]
```

Rename the existing `run` body to `assess`, returning `Assessment(report=..., resolved=resolved_by_index)`, where the ref-resolution loop records `resolved_by_index[index] = resolution.sha` using `enumerate(inputs.plans)`. The dictionaries keyed by `repo_name` inside the loop stay as they are for the `SCRIPT_REF` detail. Then:

```python
async def run(inputs: PreflightInput) -> PreflightReport:
    """The report alone, for callers that only need the answer."""
    return (await assess(inputs)).report
```

- [x] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_preflight.py -v -m integration`
Expected: PASS — the eight existing tests plus the two new ones. The existing ones passing unchanged is the point: this is a refactor with an addition, not a behaviour change.

- [x] **Step 6: Commit**

```bash
make lint && make typecheck
git add -A
git commit -s -m "refactor(api): preflight gathers and reports what it resolved"
```

---

### Task 3: Runs, created and snapshotted

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/runs.py`, `apps/api/plimsoll_api/repositories/runs.py`, `apps/api/plimsoll_api/services/runs.py`, `apps/api/plimsoll_api/routers/runs.py`
- Modify: `apps/api/plimsoll_api/security/permissions.py`, `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_runs_api.py`

**Interfaces:**
- Produces: `RunStatus`, `GeneratorStatus`, `RunResponse`, `RunStatusResponse`, `GeneratorView`; `runs_service.create(session, principal, test_id, inputs, resolved) -> Row`; `runs_service.require(session, run_id) -> Row`; `Permission.TEST_EXECUTE`.

- [x] **Step 1: Write the failing test**

`apps/api/tests/integration/test_runs_api.py`:

```python
"""Starting a run: what the API promises before any container exists."""

import uuid

import httpx
import pytest

from plimsoll_api.seed import DEMO_TEST_ID

pytestmark = pytest.mark.integration


def _start(client: httpx.Client, test_id: object = DEMO_TEST_ID) -> httpx.Response:
    return client.post(f"/api/v1/tests/{test_id}/runs")


def test_a_run_starts_queued(admin_client: httpx.Client) -> None:
    response = _start(admin_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["triggerSource"] == "API"
    assert body["degraded"] is False
    assert body["runNumber"] >= 1


def test_the_snapshot_pins_the_commit_that_will_execute(admin_client: httpx.Client) -> None:
    """Invariant 3: a branch that moves mid-run cannot change what runs."""
    snapshot = _start(admin_client).json()["configurationSnapshot"]
    assert len(snapshot["plans"][0]["commitSha"]) == 40
    assert snapshot["workload"]["virtualUsers"] == 20
    assert sum(g["users"] for g in snapshot["generators"]) == 20
    assert snapshot["targetPolicyVersion"] >= 1


def test_run_numbers_increase_within_a_project(admin_client: httpx.Client) -> None:
    first = _start(admin_client).json()["runNumber"]
    second = _start(admin_client).json()["runNumber"]
    assert second == first + 1


def test_a_run_is_read_back_and_listed(admin_client: httpx.Client) -> None:
    created = _start(admin_client).json()
    fetched = admin_client.get(f"/api/v1/runs/{created['id']}").json()
    assert fetched["id"] == created["id"]

    listed = admin_client.get(f"/api/v1/projects/{created['projectId']}/runs").json()
    assert created["id"] in [item["id"] for item in listed["items"]]


def test_the_status_endpoint_answers_cheaply(admin_client: httpx.Client) -> None:
    created = _start(admin_client).json()
    status = admin_client.get(f"/api/v1/runs/{created['id']}/status").json()
    assert status["status"] in {"QUEUED", "ALLOCATING", "STARTING", "RUNNING"}
    assert "generators" in status


def test_a_test_that_fails_preflight_starts_no_run(admin_client: httpx.Client) -> None:
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Unrunnable", "projectKey": f"U{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
        admin_client.post(
            f"/api/v1/projects/{project_id}/script-repos",
            json={
                "name": f"repo-{uuid.uuid4().hex[:6]}",
                "repoUrl": "http://script-fixture/public/plans.git",
                "planPath": "perf/checkout.jmx",
                "defaultRef": "no-such-branch",
            },
        ).json()["id"]
    )
    pool_id = next(
        str(item["id"])
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    test_id = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Cannot run",
            "configuration": {
                "virtualUsers": 10,
                "durationSeconds": 30,
                "rampUpSeconds": 5,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 10, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()["id"]

    response = _start(admin_client, test_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TEST_NOT_RUNNABLE"
    failing = [c["code"] for c in response.json()["error"]["details"]["checks"]]
    assert "SCRIPT_REF" in failing
    assert admin_client.get(f"/api/v1/projects/{project_id}/runs").json()["items"] == []


def test_an_unknown_test_cannot_be_run(admin_client: httpx.Client) -> None:
    assert _start(admin_client, uuid.uuid4()).status_code == 404


def test_a_viewer_cannot_start_a_run(viewer_client: httpx.Client) -> None:
    assert _start(viewer_client).status_code == 403
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_runs_api.py -v -m integration`
Expected: FAIL — `404` from a path that does not exist yet.

- [x] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/runs.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    ALLOCATING = "ALLOCATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class GeneratorStatus(StrEnum):
    PENDING = "PENDING"
    PROVISIONED = "PROVISIONED"
    REGISTERED = "REGISTERED"
    FETCHING = "FETCHING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # No heartbeat within the timeout. Capacity loss, never rescheduled.
    LOST = "LOST"


TERMINAL_GENERATOR_STATUSES = frozenset(
    {GeneratorStatus.COMPLETED, GeneratorStatus.FAILED, GeneratorStatus.LOST}
)


class GeneratorView(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    ordinal: int
    status: GeneratorStatus
    assigned_users: int = Field(serialization_alias="assignedUsers")
    last_heartbeat: datetime | None = Field(serialization_alias="lastHeartbeat")


class RunResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    performance_test_id: uuid.UUID = Field(serialization_alias="performanceTestId")
    run_number: int = Field(serialization_alias="runNumber")
    status: RunStatus
    trigger_source: str = Field(serialization_alias="triggerSource")
    degraded: bool
    started_at: datetime | None = Field(serialization_alias="startedAt")
    ended_at: datetime | None = Field(serialization_alias="endedAt")
    created_at: datetime = Field(serialization_alias="createdAt")
    configuration_snapshot: dict[str, Any] = Field(
        serialization_alias="configurationSnapshot"
    )
    summary: dict[str, Any] | None = None


class RunStatusResponse(BaseModel):
    """Deliberately small: this is the endpoint a client polls."""

    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    status: RunStatus
    degraded: bool
    started_at: datetime | None = Field(serialization_alias="startedAt")
    ended_at: datetime | None = Field(serialization_alias="endedAt")
    generators: list[GeneratorView]
```

- [x] **Step 4: Add the capacity-loss threshold to the workload**

The reconciler reads `maxCapacityLossPercent` from the snapshot's workload, so
the contract has to carry it or the value is never anything but the default. In
`packages/contracts/python/plimsoll_contracts/performance_tests.py`, add to
`WorkloadSpec`:

```python
    # What fraction of planned virtual users may be lost before the run is
    # failed rather than continued and marked degraded. A jsonb field on an
    # existing column, so no migration.
    max_capacity_loss_percent: int = Field(
        default=10,
        ge=0,
        le=100,
        alias="maxCapacityLossPercent",
        serialization_alias="maxCapacityLossPercent",
    )
```

and a test in `apps/api/tests/integration/test_performance_tests_api.py`:

```python
def test_the_capacity_loss_threshold_defaults_and_round_trips(
    admin_client: httpx.Client,
) -> None:
    project_id = _project_id(admin_client)
    created = _create(admin_client, project_id)
    assert created["configuration"]["maxCapacityLossPercent"] == 10

    body = _body(admin_client, project_id)
    body["configuration"]["maxCapacityLossPercent"] = 25
    strict = admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body).json()
    assert strict["configuration"]["maxCapacityLossPercent"] == 25


def test_a_threshold_above_a_hundred_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["configuration"]["maxCapacityLossPercent"] = 150
    assert admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 422
```

- [x] **Step 5: Add the permission**

In `apps/api/plimsoll_api/security/permissions.py`, add to `Permission`:

```python
    TEST_EXECUTE = "test.execute"
```

`ORG_ADMIN` holds all of `Permission` already, and `READ_PERMISSIONS` is unchanged, so a `VIEWER` does not gain it. Starting load is not the same right as editing a test.

- [x] **Step 6: Write the repository**

`apps/api/plimsoll_api/repositories/runs.py`:

```python
"""Runs, and the generators allocated to them.

Every status change is a conditional update returning the row it changed. A
caller that gets None lost a race -- to another worker, or to a stop that
arrived first -- and must not assume its transition happened.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "id, project_id, performance_test_id, run_number, status, trigger_source, "
    "started_at, ended_at, initiated_by, configuration_snapshot, summary, "
    "sla_result, degraded, created_at"
)


async def next_run_number(session: AsyncSession, project_id: uuid.UUID) -> int:
    """Taken FOR UPDATE so two concurrent starts serialise here rather than
    racing UNIQUE (project_id, run_number). A rolled-back creation gives its
    number back, which a sequence would not."""
    await session.execute(
        sa.text(
            "INSERT INTO project_run_counters (project_id, organization_id, last_run_number) "
            "SELECT id, organization_id, 0 FROM projects WHERE id = :project "
            "ON CONFLICT (project_id) DO NOTHING"
        ),
        {"project": project_id},
    )
    number: int | None = await session.scalar(
        sa.text(
            "UPDATE project_run_counters SET last_run_number = last_run_number + 1 "
            "WHERE project_id = :project RETURNING last_run_number"
        ),
        {"project": project_id},
    )
    if number is None:
        raise RuntimeError("The project has no run counter row.")
    return number


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    run_number: int,
    initiated_by: uuid.UUID | None,
    trigger_source: str,
    snapshot: dict[str, Any],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO test_runs "
                "(id, organization_id, project_id, performance_test_id, run_number, status, "
                " trigger_source, initiated_by, configuration_snapshot) "
                "VALUES (:id, :org, :project, :test, :number, 'QUEUED', :trigger, :by, "
                "        CAST(:snapshot AS jsonb)) "
                "RETURNING " + _COLUMNS
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "project": project_id,
                "test": test_id,
                "number": run_number,
                "trigger": trigger_source,
                "by": initiated_by,
                "snapshot": json.dumps(snapshot),
            },
        )
    ).one()


async def get(session: AsyncSession, run_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT " + _COLUMNS + " FROM test_runs WHERE id = :id"), {"id": run_id}
        )
    ).first()


async def list_page_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[sa.Row[Any]]:
    parameters: dict[str, Any] = {"limit": limit, "project": project_id}
    if after is None:
        statement = sa.text(
            "SELECT " + _COLUMNS + " FROM test_runs WHERE project_id = :project "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            "SELECT " + _COLUMNS + " FROM test_runs WHERE project_id = :project "
            "  AND (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def transition(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    expected: list[str],
    to: str,
    started: bool = False,
    ended: bool = False,
) -> sa.Row[Any] | None:
    """None means the run was not in `expected` -- someone else moved it."""
    return (
        await session.execute(
            sa.text(
                "UPDATE test_runs SET status = :to"
                + (", started_at = COALESCE(started_at, now())" if started else "")
                + (", ended_at = now()" if ended else "")
                + " WHERE id = :id AND status = ANY(:expected) RETURNING " + _COLUMNS
            ),
            {"id": run_id, "to": to, "expected": expected},
        )
    ).first()


async def mark_degraded(session: AsyncSession, run_id: uuid.UUID) -> None:
    await session.execute(
        sa.text("UPDATE test_runs SET degraded = TRUE WHERE id = :id"), {"id": run_id}
    )


async def set_summary(
    session: AsyncSession, run_id: uuid.UUID, summary: dict[str, Any]
) -> None:
    await session.execute(
        sa.text("UPDATE test_runs SET summary = CAST(:summary AS jsonb) WHERE id = :id"),
        {"id": run_id, "summary": json.dumps(summary)},
    )


async def insert_generators(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    pool_id: uuid.UUID,
    allocation: list[int],
) -> None:
    """Written once, before anything is provisioned. The rows are what make
    provisioning idempotent: one row per ordinal, and an ordinal that already
    carries an external_ref is never provisioned again."""
    for ordinal, users in enumerate(allocation):
        await session.execute(
            sa.text(
                "INSERT INTO run_generators "
                "(id, organization_id, run_id, pool_id, ordinal, assigned_users, status) "
                "VALUES (:id, :org, :run, :pool, :ordinal, :users, 'PENDING') "
                "ON CONFLICT (run_id, ordinal) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "run": run_id,
                "pool": pool_id,
                "ordinal": ordinal,
                "users": users,
            },
        )


async def generators_for(session: AsyncSession, run_id: uuid.UUID) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, ordinal, external_ref, assigned_users, status, "
                    "       last_heartbeat, started_at, ended_at "
                    "FROM run_generators WHERE run_id = :run ORDER BY ordinal"
                ),
                {"run": run_id},
            )
        ).all()
    )


async def attach_external_ref(
    session: AsyncSession, run_id: uuid.UUID, ordinal: int, external_ref: str
) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET external_ref = :ref, status = 'PROVISIONED' "
            "WHERE run_id = :run AND ordinal = :ordinal AND external_ref IS NULL"
        ),
        {"run": run_id, "ordinal": ordinal, "ref": external_ref},
    )


async def set_generator_status(
    session: AsyncSession,
    run_id: uuid.UUID,
    ordinal: int,
    status: str,
    *,
    heartbeat: bool = False,
) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET status = :status"
            + (", last_heartbeat = now()" if heartbeat else "")
            + " WHERE run_id = :run AND ordinal = :ordinal"
        ),
        {"run": run_id, "ordinal": ordinal, "status": status},
    )


async def touch_heartbeat(session: AsyncSession, run_id: uuid.UUID, ordinal: int) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET last_heartbeat = now() "
            "WHERE run_id = :run AND ordinal = :ordinal"
        ),
        {"run": run_id, "ordinal": ordinal},
    )
```

- [x] **Step 7: Write the service**

`apps/api/plimsoll_api/services/runs.py`:

```python
"""Creating a run, and the snapshot that makes it reproducible."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import performance_tests as tests_repo
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_api.services.preflight import Assessment, PreflightInput
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.performance_tests import WorkloadSpec

TRIGGER_API = "API"


def build_snapshot(
    inputs: PreflightInput,
    assessment: Assessment,
    *,
    workload: WorkloadSpec,
    allocation: list[int],
    target_policy_version: int,
    sla_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Everything the run needs, resolved, so nothing read after it starts
    comes from mutable configuration."""
    return {
        "plans": [
            {
                "scriptRepoId": str(plan.script_repo_id),
                "commitSha": assessment.resolved[index],
                "planPath": plan.plan_path,
                "users": plan.virtual_users,
            }
            for index, plan in enumerate(inputs.plans)
        ],
        "workload": workload.model_dump(mode="json"),
        "generators": [
            {"ordinal": ordinal, "users": users} for ordinal, users in enumerate(allocation)
        ],
        "slaRules": sla_rules,
        "targetPolicyVersion": target_policy_version,
        "allowlist": inputs.allowlist,
    }


async def create(
    session: AsyncSession,
    principal: AccessClaims,
    test_id: uuid.UUID,
    snapshot: dict[str, Any],
) -> Any:
    test = await tests_repo.get(session, test_id)
    if test is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such performance test.")

    run_number = await repo.next_run_number(session, test.project_id)
    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        project_id=test.project_id,
        test_id=test_id,
        run_number=run_number,
        initiated_by=principal.user_id,
        trigger_source=TRIGGER_API,
        snapshot=snapshot,
    )
    await repo.insert_generators(
        session,
        org_id=principal.organization_id,
        run_id=row.id,
        pool_id=uuid.UUID(snapshot["workload"]["generatorPoolId"]),
        allocation=[generator["users"] for generator in snapshot["generators"]],
    )
    await audit.record(
        session,
        principal=principal,
        action="run.created",
        entity_type="test_run",
        entity_id=row.id,
        metadata={"runNumber": run_number, "generators": len(snapshot["generators"])},
    )
    return row


async def require(session: AsyncSession, run_id: uuid.UUID) -> Any:
    row = await repo.get(session, run_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such run.")
    return row
```

- [x] **Step 8: Write the router**

`apps/api/plimsoll_api/routers/runs.py`. Allocation is imported from `plimsoll_api.allocation` — pure arithmetic that the API and the worker must agree on, so it has exactly one implementation:

```python
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.messaging import RUNS_EXECUTION, get_bus
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import preflight
from plimsoll_api.services import runs as service
from plimsoll_api.services import target_policy
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.performance_tests import WorkloadSpec
from plimsoll_contracts.runs import GeneratorView, RunResponse, RunStatusResponse
from plimsoll_api.allocation import CapacityError, allocate

router = APIRouter(tags=["runs"])


def _response(row: Any) -> RunResponse:
    return RunResponse(
        id=row.id,
        project_id=row.project_id,
        performance_test_id=row.performance_test_id,
        run_number=row.run_number,
        status=row.status,
        trigger_source=row.trigger_source,
        degraded=row.degraded,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        configuration_snapshot=row.configuration_snapshot,
        summary=row.summary,
    )


@router.post(
    "/api/v1/tests/{test_id}/runs",
    response_model=RunResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def start_run(test_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    """Preflight runs first and refuses the whole run, listing every failure.

    No session is held while it talks to Git, so this endpoint opens three short
    transactions rather than one long one.
    """
    async with session_for_org(principal.organization_id) as session:
        inputs = await preflight.gather(session, test_id)
        document = await preflight.performance_tests.require(session, test_id)
        workload = WorkloadSpec.model_validate(document.row.configuration)
        pool = await preflight.pools.require(session, workload.generator_pool_id)
        policy = await target_policy.current_policy(session)
        sla_rules = [rule.model_dump(mode="json") for rule in document.sla_rules]

    assessment = await preflight.assess(inputs)
    if not assessment.report.ok:
        raise PlimsollError(
            ErrorCode.TEST_NOT_RUNNABLE,
            "This test cannot run yet.",
            {"checks": [check.model_dump(mode="json") for check in assessment.report.checks]},
        )

    try:
        allocation = allocate(
            total_users=workload.virtual_users,
            max_generators=pool.max_generators,
            max_vus_per_generator=pool.max_vus_per_generator,
        )
    except CapacityError as exc:
        raise PlimsollError(ErrorCode.INSUFFICIENT_CAPACITY, str(exc)) from exc

    snapshot = service.build_snapshot(
        inputs,
        assessment,
        workload=workload,
        allocation=allocation,
        target_policy_version=policy.version if policy is not None else 0,
        sla_rules=sla_rules,
    )

    async with session_for_org(principal.organization_id) as session:
        row = await service.create(session, principal, test_id, snapshot)

    # After the commit: a transaction is never held open across a network call.
    try:
        await get_bus().publish(
            RUNS_EXECUTION,
            {"runId": str(row.id), "organizationId": str(principal.organization_id)},
        )
    except Exception as exc:  # noqa: BLE001 - any broker failure is the same failure
        async with session_for_org(principal.organization_id) as session:
            await repo.transition(
                session, row.id, expected=["QUEUED"], to="FAILED", ended=True
            )
            await repo.set_summary(session, row.id, {"error": "The run could not be queued."})
        raise PlimsollError(
            ErrorCode.INTERNAL, "The run could not be queued; nothing was started."
        ) from exc

    return _response(row)


@router.get(
    "/api/v1/projects/{project_id}/runs",
    response_model=Page[RunResponse],
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def list_runs(
    project_id: uuid.UUID,
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[RunResponse]:
    rows = await repo.list_page_for_project(
        session, project_id, limit=limit + 1, after=position_from(cursor)
    )
    return page_of(rows, limit, _response)


@router.get(
    "/api/v1/runs/{run_id}",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def get_run(run_id: uuid.UUID, session: TenantSession) -> RunResponse:
    return _response(await service.require(session, run_id))


@router.get(
    "/api/v1/runs/{run_id}/status",
    response_model=RunStatusResponse,
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def get_run_status(run_id: uuid.UUID, session: TenantSession) -> RunStatusResponse:
    row = await service.require(session, run_id)
    generators = await repo.generators_for(session, run_id)
    return RunStatusResponse(
        id=row.id,
        status=row.status,
        degraded=row.degraded,
        started_at=row.started_at,
        ended_at=row.ended_at,
        generators=[
            GeneratorView(
                ordinal=generator.ordinal,
                status=generator.status,
                assigned_users=generator.assigned_users,
                last_heartbeat=generator.last_heartbeat,
            )
            for generator in generators
        ],
    )
```

Register it in `main.py` beside the others, importing `runs` in the router import block and calling `app.include_router(runs.router)`.

`preflight.performance_tests` and `preflight.pools` in the snippet above are the service modules `preflight` already imports; if importing them through `preflight` reads badly, import `performance_tests` and `pools` directly in the router instead. `ErrorCode.TEST_NOT_RUNNABLE` and `ErrorCode.INSUFFICIENT_CAPACITY` already exist in `plimsoll_contracts.errors` — check, and add them mapped to `422` if not.

- [x] **Step 9: Run the tests and make sure they pass**

This task depends on `plimsoll_api.allocation` from Task 4, which is executed before this one.

Run: `uv run pytest apps/api/tests/integration/test_runs_api.py -v -m integration`
Expected: PASS — eight tests. A run stays `QUEUED` because nothing consumes the stream yet, which is what the status assertions allow for.

- [x] **Step 10: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): runs, pinned to the commit preflight resolved"
```

---

### Task 4: Allocation

**Files:**
- Create: `apps/api/plimsoll_api/allocation.py`, `apps/api/tests/unit/test_allocation.py`, `apps/worker/pyproject.toml`, `apps/worker/plimsoll_worker/__init__.py`, `apps/worker/tests/__init__.py`, `apps/worker/tests/unit/__init__.py`
- Modify: `pyproject.toml` (workspace member, testpaths), `Makefile` (`test` target)
- Test: `apps/api/tests/unit/test_allocation.py`

**Interfaces:**
- Produces: `plimsoll_api.allocation.allocate(*, total_users, max_generators, max_vus_per_generator) -> list[int]`, `CapacityError`.

The allocator lives in `plimsoll_api` even though the worker is its busiest
caller. Both processes must agree on how many generators a run gets, and every
other dependency in this slice runs worker to api -- putting it in the worker
would make the API import the worker package and stop the two being separable.

- [x] **Step 1: Write the failing test**

`apps/api/tests/unit/test_allocation.py`:

```python
"""Virtual users across generators. The totals must reconcile exactly."""

import pytest

from plimsoll_api.allocation import CapacityError, allocate


def test_an_even_split() -> None:
    assert allocate(total_users=100, max_generators=4, max_vus_per_generator=50) == [50, 50]


def test_the_remainder_goes_to_the_earliest_generators() -> None:
    # 10 users over 3 generators is 4, 3, 3 -- never 3, 3, 3 with one lost.
    assert allocate(total_users=10, max_generators=3, max_vus_per_generator=4) == [4, 3, 3]


def test_every_allocation_sums_to_the_request() -> None:
    for total in range(1, 200):
        allocation = allocate(
            total_users=total, max_generators=50, max_vus_per_generator=7
        )
        assert sum(allocation) == total


def test_no_generator_exceeds_its_ceiling() -> None:
    allocation = allocate(total_users=1000, max_generators=50, max_vus_per_generator=300)
    assert max(allocation) <= 300


def test_one_generator_is_enough_for_a_small_test() -> None:
    assert allocate(total_users=5, max_generators=10, max_vus_per_generator=500) == [5]


def test_a_request_beyond_the_pool_is_refused() -> None:
    with pytest.raises(CapacityError) as raised:
        allocate(total_users=10_000, max_generators=2, max_vus_per_generator=500)
    assert "1000" in str(raised.value)


def test_zero_users_is_refused() -> None:
    with pytest.raises(CapacityError):
        allocate(total_users=0, max_generators=2, max_vus_per_generator=500)
```

- [x] **Step 2: Create the package**

`apps/worker/pyproject.toml`:

```toml
[project]
name = "plimsoll-worker"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "plimsoll-api",
    "plimsoll-contracts",
    "docker>=7.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["plimsoll_worker"]
```

In the root `pyproject.toml`: add `"apps/worker"` to `[tool.uv.workspace] members`, add `plimsoll-worker = { workspace = true }` to `[tool.uv.sources]`, add `"plimsoll-worker"` to the root `dependencies`, add `"plimsoll_worker"` to `known-first-party`, and extend `testpaths` to `["apps/api/tests", "apps/worker/tests"]`.

In the `Makefile`, `make test` must reach the new unit tests:

```make
test:
	$(UV) pytest apps/api/tests/unit apps/worker/tests/unit -v
```

Run `uv sync` and then the test: `uv run pytest apps/api/tests/unit/test_allocation.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.allocation`.

- [x] **Step 3: Write the allocator**

`apps/api/plimsoll_api/allocation.py`:

```python
"""How many generators, and how many virtual users on each.

JMeter allocates an OS thread per virtual user, so `max_vus_per_generator` is a
real ceiling rather than a formality, and the arithmetic here decides whether a
run is honest about the load it produced.
"""

from __future__ import annotations

import math


class CapacityError(Exception):
    """The pool cannot supply what the test asks for."""


def allocate(
    *, total_users: int, max_generators: int, max_vus_per_generator: int
) -> list[int]:
    if total_users < 1:
        raise CapacityError("A run needs at least one virtual user.")
    if max_generators < 1 or max_vus_per_generator < 1:
        raise CapacityError("The pool declares no capacity.")

    ceiling = max_generators * max_vus_per_generator
    if total_users > ceiling:
        raise CapacityError(
            f"The test asks for {total_users} virtual users; the pool can supply {ceiling}."
        )

    generators = math.ceil(total_users / max_vus_per_generator)
    base, remainder = divmod(total_users, generators)
    # The remainder is handed out one user at a time to the earliest generators,
    # so the allocation sums to exactly what was asked for. Rounding each share
    # independently is how a 1,000-user test quietly becomes a 998-user test.
    return [base + (1 if index < remainder else 0) for index in range(generators)]
```

- [x] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_allocation.py -v`
Expected: PASS — seven tests. `test_every_allocation_sums_to_the_request` is the one that matters; it is a property over 199 cases rather than three examples.

- [x] **Step 5: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(worker): allocate virtual users so the totals reconcile"
```

---

### Task 5: Run-scoped tokens and the agent channel

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/agent.py`, `apps/api/plimsoll_api/routers/agent.py`
- Modify: `apps/api/plimsoll_api/security/tokens.py`, `apps/api/plimsoll_api/repositories/runs.py`, `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_agent_channel.py`

**Interfaces:**
- Produces: `issue_agent_token(run_id, ordinal, org_id, ttl_seconds) -> str`, `decode_agent_token(token) -> AgentClaims`, the protocol models, and `WS /api/v1/agent/runs/{run_id}`.

- [x] **Step 1: Write the failing test**

`apps/api/tests/integration/test_agent_channel.py`:

```python
"""The agent's only door into the control plane.

A generator runs a user-supplied plan, so what this endpoint refuses matters as
much as what it accepts.
"""

import json
import uuid

import pytest
import websockets

from plimsoll_api.security.tokens import issue_agent_token

pytestmark = pytest.mark.integration

WS_URL = "ws://localhost:8000/api/v1/agent/runs"


async def _open(run_id: uuid.UUID, token: str) -> websockets.ClientConnection:
    return await websockets.connect(f"{WS_URL}/{run_id}", additional_headers={
        "Authorization": f"Bearer {token}"
    })


async def test_an_agent_registers_and_is_acknowledged(admin_client, admin_org) -> None:
    run_id = uuid.UUID(admin_client.post("/api/v1/tests/%s/runs" % DEMO_TEST_ID).json()["id"])
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        acknowledgement = json.loads(await socket.recv())

    assert acknowledgement["type"] == "registered"
    assert acknowledgement["desiredState"] in {"QUEUED", "ALLOCATING", "STARTING"}


async def test_a_heartbeat_is_answered_with_the_desired_state(admin_client, admin_org) -> None:
    run_id = uuid.UUID(admin_client.post("/api/v1/tests/%s/runs" % DEMO_TEST_ID).json()["id"])
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "heartbeat"}))
        beat = json.loads(await socket.recv())

    assert beat["type"] == "heartbeat_ack"
    assert "desiredState" in beat

    status = admin_client.get(f"/api/v1/runs/{run_id}/status").json()
    assert status["generators"][0]["lastHeartbeat"] is not None


async def test_a_token_for_another_run_is_refused(admin_client, admin_org) -> None:
    """The token names one run; the path names another."""
    run_id = uuid.UUID(admin_client.post("/api/v1/tests/%s/runs" % DEMO_TEST_ID).json()["id"])
    token = issue_agent_token(uuid.uuid4(), ordinal=0, org_id=admin_org, ttl_seconds=300)

    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with await _open(run_id, token):
            pass


async def test_an_ordinary_access_token_is_refused(admin_client, admin_org) -> None:
    """An access token has no `aud: agent`, and this door takes nothing else."""
    from plimsoll_api.security.tokens import issue_access_token

    run_id = uuid.UUID(admin_client.post("/api/v1/tests/%s/runs" % DEMO_TEST_ID).json()["id"])
    token = issue_access_token(uuid.uuid4(), admin_org, "ORG_ADMIN")

    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with await _open(run_id, token):
            pass


async def test_a_state_report_lands_on_the_generator_row(admin_client, admin_org) -> None:
    run_id = uuid.UUID(admin_client.post("/api/v1/tests/%s/runs" % DEMO_TEST_ID).json()["id"])
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "state", "state": "READY", "reason": None}))
        await socket.recv()

    status = admin_client.get(f"/api/v1/runs/{run_id}/status").json()
    assert status["generators"][0]["status"] == "READY"
```

Add `from plimsoll_api.seed import DEMO_TEST_ID` at the top, and add `"websockets>=13"` to the root `[dependency-groups] dev` list.

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_agent_channel.py -v -m integration`
Expected: FAIL — the WebSocket route does not exist, so the handshake is rejected.

- [x] **Step 3: Write the token**

Append to `apps/api/plimsoll_api/security/tokens.py`:

```python
AGENT_AUDIENCE = "agent"


@dataclass(frozen=True)
class AgentClaims:
    run_id: uuid.UUID
    ordinal: int
    organization_id: uuid.UUID


def issue_agent_token(
    run_id: uuid.UUID, *, ordinal: int, org_id: uuid.UUID, ttl_seconds: int
) -> str:
    """Scoped to one run and one ordinal, and expiring with the run.

    There is no long-lived registration secret on a generator because a
    generator does not outlive its run -- so neither does its credential.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(run_id),
        "ordinal": ordinal,
        "org": str(org_id),
        "aud": AGENT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=ALGORITHM)


def decode_agent_token(token: str) -> AgentClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            audience=AGENT_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    try:
        return AgentClaims(
            run_id=uuid.UUID(payload["sub"]),
            ordinal=int(payload["ordinal"]),
            organization_id=uuid.UUID(payload["org"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("The token is missing a required claim.") from exc
```

`decode_access_token` must not accept an agent token: add `options={"verify_aud": False}` is **not** the fix — instead reject a payload carrying `aud`:

```python
    if payload.get("aud"):
        raise TokenError("This token is not an access token.")
```

placed immediately before the `AccessClaims` construction.

- [x] **Step 4: Write the protocol**

`packages/contracts/python/plimsoll_contracts/agent.py`:

```python
"""The agent wire protocol, defined once and imported by both ends.

The agent is the only component outside the control plane's trust boundary, so
every frame it sends is parsed into one of these before anything acts on it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentState(StrEnum):
    STARTING = "STARTING"
    FETCHING = "FETCHING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Command(StrEnum):
    WAIT = "WAIT"
    START = "START"
    STOP = "STOP"
    CANCEL = "CANCEL"


class Register(BaseModel):
    type: Literal["register"] = "register"
    ordinal: int
    version: str


class StateReport(BaseModel):
    type: Literal["state"] = "state"
    state: AgentState
    reason: str | None = None


class Heartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"


class Registered(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["registered"] = "registered"
    desired_state: str = Field(serialization_alias="desiredState")
    command: Command
    # Everything the agent needs that is not in the bundle.
    assigned_users: int = Field(serialization_alias="assignedUsers")
    duration_seconds: int = Field(serialization_alias="durationSeconds")
    ramp_up_seconds: int = Field(serialization_alias="rampUpSeconds")


class HeartbeatAck(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    desired_state: str = Field(serialization_alias="desiredState")
    command: Command


class CommandFrame(BaseModel):
    type: Literal["command"] = "command"
    command: Command


class Accepted(BaseModel):
    type: Literal["accepted"] = "accepted"
```

- [x] **Step 5: Write the channel**

`apps/api/plimsoll_api/routers/agent.py`:

```python
"""The agent's outbound-only WebSocket.

Generators never accept inbound connections, which is what lets them run in a
locked-down network. Commands travel the other way down this same socket.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from plimsoll_api.db.session import session_for_org
from plimsoll_api.messaging import get_bus, run_channel
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import AgentClaims, TokenError, decode_agent_token
from plimsoll_contracts.agent import (
    Command,
    HeartbeatAck,
    Registered,
    StateReport,
)
from plimsoll_contracts.runs import RunStatus

router = APIRouter()

COMMAND_FOR_STATUS = {
    RunStatus.QUEUED: Command.WAIT,
    RunStatus.ALLOCATING: Command.WAIT,
    RunStatus.STARTING: Command.WAIT,
    RunStatus.RUNNING: Command.START,
    RunStatus.STOPPING: Command.STOP,
    RunStatus.COMPLETED: Command.STOP,
    RunStatus.FAILED: Command.CANCEL,
    RunStatus.CANCELLED: Command.CANCEL,
}


def _claims(websocket: WebSocket, run_id: uuid.UUID) -> AgentClaims | None:
    header = websocket.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        claims = decode_agent_token(header.removeprefix("Bearer "))
    except TokenError:
        return None
    # The token names its run. A token for another run is not a token for this
    # one, however valid its signature.
    return claims if claims.run_id == run_id else None


async def _desired(claims: AgentClaims) -> tuple[str, Command]:
    async with session_for_org(claims.organization_id) as session:
        run = await repo.get(session, claims.run_id)
    if run is None:
        return RunStatus.CANCELLED, Command.CANCEL
    return run.status, COMMAND_FOR_STATUS.get(RunStatus(run.status), Command.WAIT)


@router.websocket("/api/v1/agent/runs/{run_id}")
async def agent_channel(websocket: WebSocket, run_id: uuid.UUID) -> None:
    claims = _claims(websocket, run_id)
    if claims is None:
        # Refused before the upgrade: an unauthenticated socket never opens.
        await websocket.close(code=4401)
        return
    await websocket.accept()

    pushes: asyncio.Task[None] | None = None
    try:
        while True:
            raw = await websocket.receive_json()
            kind = raw.get("type")

            if kind == "register":
                async with session_for_org(claims.organization_id) as session:
                    await repo.set_generator_status(
                        session, claims.run_id, claims.ordinal, "REGISTERED", heartbeat=True
                    )
                    run = await repo.get(session, claims.run_id)
                    generators = await repo.generators_for(session, claims.run_id)
                mine = next(g for g in generators if g.ordinal == claims.ordinal)
                workload = run.configuration_snapshot["workload"]
                desired, command = run.status, COMMAND_FOR_STATUS.get(
                    RunStatus(run.status), Command.WAIT
                )
                await websocket.send_text(
                    Registered(
                        desired_state=desired,
                        command=command,
                        assigned_users=mine.assigned_users,
                        duration_seconds=workload["durationSeconds"],
                        ramp_up_seconds=workload["rampUpSeconds"],
                    ).model_dump_json(by_alias=True)
                )
                pushes = asyncio.create_task(_push_commands(websocket, claims))

            elif kind == "heartbeat":
                async with session_for_org(claims.organization_id) as session:
                    await repo.touch_heartbeat(session, claims.run_id, claims.ordinal)
                desired, command = await _desired(claims)
                await websocket.send_text(
                    HeartbeatAck(desired_state=desired, command=command).model_dump_json(
                        by_alias=True
                    )
                )

            elif kind == "state":
                report = StateReport.model_validate(raw)
                async with session_for_org(claims.organization_id) as session:
                    await repo.set_generator_status(
                        session, claims.run_id, claims.ordinal, report.state, heartbeat=True
                    )
                await websocket.send_text('{"type":"accepted"}')

    except WebSocketDisconnect:
        # A disconnect is not a failure. The generator is judged by its
        # heartbeat, so silence is what marks it lost, not a closed socket.
        pass
    finally:
        if pushes is not None:
            pushes.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pushes


async def _push_commands(websocket: WebSocket, claims: AgentClaims) -> None:
    """Announcements are a nudge, not the truth.

    The heartbeat acknowledgement carries the desired state too, so a push that
    never arrives costs one interval rather than the run.
    """
    async with get_bus().listen(run_channel(claims.run_id)) as messages:
        async for message in messages:
            await websocket.send_text(
                f'{{"type":"command","command":"{message["command"]}"}}'
            )
```

Register it in `main.py` with `app.include_router(agent.router)`.

- [x] **Step 6: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_agent_channel.py -v -m integration`
Expected: PASS — five tests. The two refusal tests are the ones that matter: if either passes a connection, the run-scoped token is decorative.

- [x] **Step 7: Commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): the agent channel, scoped to one run and one ordinal"
```

---

### Task 6: The agent, and the generator image

**Files:**
- Create: `apps/agent/pyproject.toml`, `apps/agent/plimsoll_agent/__init__.py`, `apps/agent/plimsoll_agent/__main__.py`, `apps/agent/plimsoll_agent/channel.py`, `apps/agent/plimsoll_agent/lifecycle.py`, `apps/agent/tests/__init__.py`, `apps/agent/tests/unit/__init__.py`, `apps/agent/tests/unit/test_lifecycle.py`, `infrastructure/docker/generator.Dockerfile`
- Modify: `pyproject.toml`, `Makefile`, `infrastructure/docker/docker-compose.yml`, `apps/api/plimsoll_api/seed.py`
- Test: `apps/agent/tests/unit/test_lifecycle.py`

**Interfaces:**
- Produces: image `ghcr.io/ultron13/generator:dev`; `plimsoll_agent.lifecycle.next_action(command, state) -> Action`; an agent that registers, heartbeats, waits out its duration, and reports `COMPLETED`.

- [ ] **Step 1: Write the failing test**

The agent's decisions are a pure function, so they are tested without a socket.

`apps/agent/tests/unit/test_lifecycle.py`:

```python
from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_contracts.agent import AgentState, Command


def test_it_waits_until_told_to_start() -> None:
    assert next_action(Command.WAIT, AgentState.READY) is Action.HOLD


def test_it_starts_when_commanded() -> None:
    assert next_action(Command.START, AgentState.READY) is Action.RUN


def test_it_does_not_start_twice() -> None:
    """A repeated START -- a push and then a heartbeat carrying the same state
    -- must not launch a second execution."""
    assert next_action(Command.START, AgentState.RUNNING) is Action.HOLD


def test_a_stop_while_running_winds_down() -> None:
    assert next_action(Command.STOP, AgentState.RUNNING) is Action.WIND_DOWN


def test_a_stop_before_running_finishes_immediately() -> None:
    assert next_action(Command.STOP, AgentState.READY) is Action.FINISH


def test_a_cancel_abandons_from_anywhere() -> None:
    for state in (AgentState.FETCHING, AgentState.READY, AgentState.RUNNING):
        assert next_action(Command.CANCEL, state) is Action.ABANDON


def test_nothing_follows_a_terminal_state() -> None:
    assert next_action(Command.START, AgentState.COMPLETED) is Action.HOLD
```

- [ ] **Step 2: Create the package and run the test**

`apps/agent/pyproject.toml`:

```toml
[project]
name = "plimsoll-agent"
version = "0.1.0"
requires-python = ">=3.12"
# Deliberately small. The agent runs beside a user-supplied plan, so it carries
# no database driver, no key provider, and no object-store client.
dependencies = [
    "plimsoll-contracts",
    "websockets>=13",
    "httpx>=0.27",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["plimsoll_agent"]
```

Add `"apps/agent"` to the workspace members, `plimsoll-agent = { workspace = true }` to sources, `"plimsoll-agent"` to the root dependencies, `"plimsoll_agent"` to `known-first-party`, and `"apps/agent/tests"` to `testpaths`. Extend `make test` to `apps/api/tests/unit apps/worker/tests/unit apps/agent/tests/unit`.

Run: `uv sync && uv run pytest apps/agent/tests/unit/test_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_agent.lifecycle`.

- [ ] **Step 3: Write the lifecycle and the channel**

`apps/agent/plimsoll_agent/lifecycle.py`:

```python
"""What to do next, given what the control plane wants and where we are.

Pure, and therefore the part that is actually tested. Everything around it is
sockets and subprocesses.
"""

from __future__ import annotations

from enum import Enum, auto

from plimsoll_contracts.agent import AgentState, Command

TERMINAL = {AgentState.COMPLETED, AgentState.FAILED}


class Action(Enum):
    HOLD = auto()
    RUN = auto()
    WIND_DOWN = auto()
    FINISH = auto()
    ABANDON = auto()


def next_action(command: Command, state: AgentState) -> Action:
    if state in TERMINAL:
        return Action.HOLD
    if command is Command.CANCEL:
        return Action.ABANDON
    if command is Command.STOP:
        # Nothing has started, so there is nothing to wind down.
        return Action.WIND_DOWN if state is AgentState.RUNNING else Action.FINISH
    if command is Command.START and state is AgentState.READY:
        return Action.RUN
    return Action.HOLD
```

`apps/agent/plimsoll_agent/channel.py`:

```python
"""The socket half: connect, register, heartbeat, and read commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import websockets

from plimsoll_contracts.agent import AgentState, Command, Heartbeat, Register, StateReport

HEARTBEAT_SECONDS = 10
VERSION = "0.1.0"


class Channel:
    def __init__(self, socket: websockets.ClientConnection) -> None:
        self._socket = socket
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(self, model: Register | StateReport | Heartbeat) -> None:
        await self._socket.send(model.model_dump_json())

    async def report(self, state: AgentState, reason: str | None = None) -> None:
        await self.send(StateReport(state=state, reason=reason))

    async def receive(self) -> dict[str, Any]:
        message: dict[str, Any] = json.loads(await self._socket.recv())
        return message

    async def heartbeat_forever(self, on_command: Any) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await self.send(Heartbeat())


@asynccontextmanager
async def connect(api_url: str, run_id: str, token: str) -> AsyncIterator[Channel]:
    url = api_url.replace("http://", "ws://").replace("https://", "wss://")
    async with websockets.connect(
        f"{url}/api/v1/agent/runs/{run_id}",
        additional_headers={"Authorization": f"Bearer {token}"},
        # A generator with a wedged socket is worse than one that reconnects.
        open_timeout=30,
        ping_interval=20,
    ) as socket:
        yield Channel(socket)


def command_of(message: dict[str, Any]) -> Command | None:
    value = message.get("command")
    return Command(value) if value else None
```

`apps/agent/plimsoll_agent/__main__.py`:

```python
"""plimsoll-agent.

S3a: register, hold until told to start, wait out the duration, report
COMPLETED. S3b replaces the waiting with JMeter.
"""

from __future__ import annotations

import asyncio
import os
import sys

from plimsoll_agent.channel import Channel, command_of, connect
from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_contracts.agent import AgentState, Command, Heartbeat, Register


async def _execute(duration_seconds: int, stop: asyncio.Event) -> bool:
    """Returns whether it ran to completion rather than being stopped."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=duration_seconds)
    except TimeoutError:
        return True
    return False


async def run_agent() -> int:
    api_url = os.environ["PLIMSOLL_API_URL"]
    run_id = os.environ["PLIMSOLL_RUN_ID"]
    token = os.environ["PLIMSOLL_RUN_TOKEN"]
    ordinal = int(os.environ["PLIMSOLL_ORDINAL"])

    async with connect(api_url, run_id, token) as channel:
        await channel.send(Register(ordinal=ordinal, version="0.1.0"))
        registered = await channel.receive()
        duration = int(registered["durationSeconds"])

        await channel.report(AgentState.FETCHING)
        await channel.receive()
        # S3b fetches the bundle here.
        state = AgentState.READY
        await channel.report(state)
        await channel.receive()

        stop = asyncio.Event()
        beats = asyncio.create_task(_beat(channel))
        try:
            while True:
                message = await channel.receive()
                command = command_of(message) or Command.WAIT
                action = next_action(command, state)

                if action is Action.RUN:
                    state = AgentState.RUNNING
                    await channel.report(state)
                    completed = await _execute(duration, stop)
                    state = AgentState.COMPLETED
                    await channel.report(state, None if completed else "stopped")
                    return 0
                if action is Action.WIND_DOWN:
                    stop.set()
                if action in (Action.FINISH, Action.ABANDON):
                    state = AgentState.COMPLETED
                    await channel.report(state, "stopped before starting")
                    return 0
        finally:
            beats.cancel()


async def _beat(channel: Channel) -> None:
    while True:
        await asyncio.sleep(10)
        await channel.send(Heartbeat())


def main() -> int:
    return asyncio.run(run_agent())


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Write the image**

`infrastructure/docker/generator.Dockerfile`:

```dockerfile
# The generator image. S3a carries the agent only; S3b adds a JRE and JMeter.
# Nothing here downloads anything at run time -- an air-gapped deployment is a
# stated v1.0 goal and is cheap to keep, expensive to recover.
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
COPY apps/agent/pyproject.toml apps/agent/
COPY packages/contracts/python/pyproject.toml packages/contracts/python/
RUN uv sync --locked --no-dev --no-install-workspace --package plimsoll-agent

COPY packages/contracts/python packages/contracts/python
COPY apps/agent apps/agent
RUN uv sync --locked --no-dev --package plimsoll-agent

RUN useradd --uid 10001 --create-home plimsoll && chown -R 10001:10001 /srv
ENV HOME=/home/plimsoll UV_NO_SYNC=1 UV_FROZEN=1
USER 10001
CMD ["uv", "run", "python", "-m", "plimsoll_agent"]
```

In `docker-compose.yml`, add a build-only service so one `make dev` builds it and nothing tries to run it:

```yaml
  generator-image:
    image: ghcr.io/ultron13/generator:dev
    build:
      context: ../..
      dockerfile: infrastructure/docker/generator.Dockerfile
    # Never started: generators are created per run by the worker. This entry
    # exists so `make dev` builds the image on a clean machine.
    profiles: ["images"]
```

In the `Makefile`, build it as part of `dev`, before the stack comes up:

```make
dev:
	$(COMPOSE) build generator-image
	$(COMPOSE) up --build -d
```

In `seed.py`, point the demo pool at the image that now exists:

```python
DEMO_POOL_CONFIG = '{"image": "ghcr.io/ultron13/generator:dev"}'
```

- [ ] **Step 5: Run the tests and build**

Run: `uv run pytest apps/agent/tests/unit/test_lifecycle.py -v`
Expected: PASS — seven tests.

Run: `make dev-down && make dev && docker image inspect ghcr.io/ultron13/generator:dev --format '{{.Id}}'`
Expected: an image id. A `--package` flag rejected by uv means the installed version predates it; drop `--package` and let the image install the whole workspace, and note in a comment that it is heavier than it needs to be.

- [ ] **Step 6: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(agent): an agent that registers, holds, and reports"
```

---

### Task 7: The Docker runtime

**Files:**
- Create: `apps/worker/plimsoll_worker/runtime/__init__.py`, `apps/worker/plimsoll_worker/runtime/base.py`, `apps/worker/plimsoll_worker/runtime/docker.py`, `apps/worker/tests/integration/__init__.py`, `apps/worker/tests/integration/test_docker_runtime.py`
- Modify: `Makefile` (`test-int`)
- Test: `apps/worker/tests/integration/test_docker_runtime.py`

**Interfaces:**
- Produces: `GeneratorSpec`, `GeneratorHandle`, `GeneratorRuntime` protocol, `DockerRuntime`.

- [ ] **Step 1: Write the failing test**

`apps/worker/tests/integration/test_docker_runtime.py`:

```python
"""Containers, created and removed against the real daemon."""

import uuid

import pytest

from plimsoll_worker.runtime.docker import DockerRuntime
from plimsoll_worker.runtime.base import GeneratorSpec

pytestmark = pytest.mark.integration

IMAGE = "ghcr.io/ultron13/generator:dev"


def _spec(ordinal: int) -> GeneratorSpec:
    return GeneratorSpec(
        run_id=uuid.uuid4(),
        ordinal=ordinal,
        image=IMAGE,
        network="plimsoll_default",
        environment={"PLIMSOLL_SLEEP_FOREVER": "1"},
        labels={"plimsoll.test": "true"},
    )


async def test_a_generator_is_created_and_removed() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    try:
        assert len(handles) == 1
        assert handles[0].external_ref
        statuses = await runtime.status(handles)
        assert statuses[0].present is True
    finally:
        await runtime.teardown(handles)

    assert (await runtime.status(handles))[0].present is False


async def test_a_generator_never_restarts() -> None:
    """Invariant 6: a restarted generator resets virtual-user state mid-test
    and silently corrupts the run."""
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    try:
        assert await runtime.restart_policy(handles[0]) in ("", "no")
    finally:
        await runtime.teardown(handles)


async def test_tearing_down_twice_is_not_an_error() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    await runtime.teardown(handles)
    await runtime.teardown(handles)


async def test_every_generator_gets_its_own_container() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0), _spec(1)])
    try:
        assert len({handle.external_ref for handle in handles}) == 2
    finally:
        await runtime.teardown(handles)
```

Extend `make test-int` to include the worker's integration tests:

```make
test-int:
	$(UV) pytest apps/api/tests/integration apps/worker/tests/integration -v -m integration
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/worker/tests/integration/test_docker_runtime.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_worker.runtime`.

- [ ] **Step 3: Write the runtime**

`apps/worker/plimsoll_worker/runtime/base.py`:

```python
"""One interface, two implementations -- Docker now, Kubernetes with the Helm
chart. The generator image is identical in both; only the launcher differs,
which is what keeps `make dev` honest about production."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class GeneratorSpec:
    run_id: uuid.UUID
    ordinal: int
    image: str
    network: str
    environment: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    memory_limit: str | None = None
    cpu_limit: float | None = None


@dataclass(frozen=True)
class GeneratorHandle:
    ordinal: int
    external_ref: str


@dataclass(frozen=True)
class GeneratorState:
    ordinal: int
    present: bool
    exit_code: int | None = None


class GeneratorRuntime(Protocol):
    async def provision(self, specs: list[GeneratorSpec]) -> list[GeneratorHandle]: ...

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]: ...

    async def teardown(self, handles: list[GeneratorHandle]) -> None: ...
```

`apps/worker/plimsoll_worker/runtime/docker.py`:

```python
"""Generators as sibling containers, created through the mounted socket.

The socket is root-equivalent on the host, which is why it is mounted into the
worker and nowhere else. Docker-in-Docker would need privileges too, and buys
nothing here.

The SDK is synchronous, so every call crosses into a thread rather than
blocking the loop that is also serving heartbeats.
"""

from __future__ import annotations

import asyncio

import docker
from docker.errors import DockerException, NotFound

from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec, GeneratorState


class DockerRuntime:
    def __init__(self) -> None:
        self._client = docker.from_env()

    def _create(self, spec: GeneratorSpec) -> str:
        container = self._client.containers.run(
            spec.image,
            detach=True,
            name=f"plimsoll-gen-{spec.run_id}-{spec.ordinal}",
            network=spec.network,
            environment=spec.environment,
            labels={**spec.labels, "plimsoll.run": str(spec.run_id)},
            # Invariant 6. A lost generator is capacity loss and must surface as
            # such, never be papered over by a restart.
            restart_policy={"Name": "no"},
            mem_limit=spec.memory_limit,
            nano_cpus=int(spec.cpu_limit * 1_000_000_000) if spec.cpu_limit else None,
        )
        return str(container.id)

    async def provision(self, specs: list[GeneratorSpec]) -> list[GeneratorHandle]:
        handles = []
        for spec in specs:
            reference = await asyncio.to_thread(self._create, spec)
            handles.append(GeneratorHandle(ordinal=spec.ordinal, external_ref=reference))
        return handles

    def _inspect(self, handle: GeneratorHandle) -> GeneratorState:
        try:
            container = self._client.containers.get(handle.external_ref)
        except NotFound:
            return GeneratorState(ordinal=handle.ordinal, present=False)
        container.reload()
        return GeneratorState(
            ordinal=handle.ordinal,
            present=container.status in ("created", "running", "restarting", "paused"),
            exit_code=container.attrs.get("State", {}).get("ExitCode"),
        )

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]:
        return [await asyncio.to_thread(self._inspect, handle) for handle in handles]

    def _remove(self, handle: GeneratorHandle) -> None:
        try:
            self._client.containers.get(handle.external_ref).remove(force=True)
        except (NotFound, DockerException):
            # Already gone is the outcome teardown wanted.
            pass

    async def teardown(self, handles: list[GeneratorHandle]) -> None:
        for handle in handles:
            await asyncio.to_thread(self._remove, handle)

    async def restart_policy(self, handle: GeneratorHandle) -> str:
        def read() -> str:
            container = self._client.containers.get(handle.external_ref)
            policy = container.attrs["HostConfig"].get("RestartPolicy") or {}
            return str(policy.get("Name", ""))

        return await asyncio.to_thread(read)
```

The agent exits immediately without its environment, so the test's containers would vanish before assertions run. Give the agent a hold for exactly this: in `apps/agent/plimsoll_agent/__main__.py`, at the top of `main()`:

```python
def main() -> int:
    if os.environ.get("PLIMSOLL_SLEEP_FOREVER"):
        # A generator with no run to join, used by the runtime's own tests.
        asyncio.run(asyncio.sleep(3600))
        return 0
    return asyncio.run(run_agent())
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/worker/tests/integration/test_docker_runtime.py -v -m integration`
Expected: PASS — four tests. If the network name is wrong, list it with `docker network ls | grep plimsoll` and use what compose actually created.

- [ ] **Step 5: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(worker): generators as containers that never restart"
```

---

### Task 8: The reconciler

**Files:**
- Create: `apps/worker/plimsoll_worker/reconciler.py`, `apps/worker/plimsoll_worker/__main__.py`, `apps/worker/tests/unit/test_reconciler.py`
- Modify: `infrastructure/docker/docker-compose.yml`
- Test: `apps/worker/tests/unit/test_reconciler.py`, `apps/api/tests/integration/test_run_execution.py`

**Interfaces:**
- Produces: `RunView`, `GeneratorRow`, `Decision`, `decide(view) -> Decision`; `Orchestrator.reconcile(run_id, org_id)`; the `worker` compose service.

- [ ] **Step 1: Write the failing unit test**

The decision is pure; everything that touches Docker or the database is applied around it.

`apps/worker/tests/unit/test_reconciler.py`:

```python
from datetime import UTC, datetime, timedelta

from plimsoll_worker.reconciler import Decision, GeneratorRow, RunView, decide

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def _view(status: str, generators: list[GeneratorRow], **kwargs: object) -> RunView:
    return RunView(
        status=status,
        generators=generators,
        now=NOW,
        duration_seconds=60,
        started_at=kwargs.get("started_at"),
        max_capacity_loss_percent=kwargs.get("max_capacity_loss_percent", 10),
    )


def _generator(ordinal: int, status: str, users: int = 10, beat_age: int = 0) -> GeneratorRow:
    return GeneratorRow(
        ordinal=ordinal,
        status=status,
        assigned_users=users,
        external_ref=None if status == "PENDING" else f"container-{ordinal}",
        last_heartbeat=NOW - timedelta(seconds=beat_age),
    )


def test_a_queued_run_is_provisioned() -> None:
    view = _view("QUEUED", [_generator(0, "PENDING"), _generator(1, "PENDING")])
    assert decide(view) is Decision.PROVISION


def test_a_run_whose_generators_exist_is_not_provisioned_again() -> None:
    """At-least-once delivery means this message may be the second copy."""
    view = _view("ALLOCATING", [_generator(0, "PROVISIONED"), _generator(1, "PROVISIONED")])
    assert decide(view) is Decision.WAIT


def test_all_ready_starts_the_run() -> None:
    view = _view("STARTING", [_generator(0, "READY"), _generator(1, "READY")])
    assert decide(view) is Decision.START


def test_one_generator_short_of_ready_does_not_start() -> None:
    """A staggered start smears the ramp and quietly distorts the result."""
    view = _view("STARTING", [_generator(0, "READY"), _generator(1, "FETCHING")])
    assert decide(view) is Decision.WAIT


def test_all_terminal_finishes_the_run() -> None:
    view = _view("RUNNING", [_generator(0, "COMPLETED"), _generator(1, "COMPLETED")])
    assert decide(view) is Decision.FINISH


def test_a_silent_generator_is_lost() -> None:
    view = _view("RUNNING", [_generator(0, "RUNNING"), _generator(1, "RUNNING", beat_age=45)])
    assert decide(view) is Decision.MARK_LOST


def test_capacity_loss_below_the_threshold_continues_degraded() -> None:
    view = _view(
        "RUNNING",
        [_generator(0, "RUNNING", users=95), _generator(1, "LOST", users=5)],
    )
    assert decide(view) is Decision.CONTINUE_DEGRADED


def test_capacity_loss_at_the_threshold_fails_the_run() -> None:
    view = _view(
        "RUNNING",
        [_generator(0, "RUNNING", users=90), _generator(1, "LOST", users=10)],
    )
    assert decide(view) is Decision.FAIL


def test_a_stopping_run_waits_for_its_generators() -> None:
    view = _view("STOPPING", [_generator(0, "RUNNING"), _generator(1, "COMPLETED")])
    assert decide(view) is Decision.WAIT


def test_a_terminal_run_needs_nothing() -> None:
    for status in ("COMPLETED", "FAILED", "CANCELLED"):
        assert decide(_view(status, [_generator(0, "COMPLETED")])) is Decision.DONE
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/worker/tests/unit/test_reconciler.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_worker.reconciler`.

- [ ] **Step 3: Write the decision**

`apps/worker/plimsoll_worker/reconciler.py`:

```python
"""What the run needs next, decided from persisted state alone.

The worker is a reconciler rather than a script, and this function is why: a
duplicate delivery, a restarted worker, and a timer tick all arrive here, and
all of them are answered from the same picture. Nothing important lives in
worker memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto

from plimsoll_contracts.runs import (
    TERMINAL_GENERATOR_STATUSES,
    TERMINAL_RUN_STATUSES,
    GeneratorStatus,
    RunStatus,
)

HEARTBEAT_TIMEOUT = timedelta(seconds=35)


class Decision(Enum):
    PROVISION = auto()
    START = auto()
    MARK_LOST = auto()
    CONTINUE_DEGRADED = auto()
    FAIL = auto()
    FINISH = auto()
    WAIT = auto()
    DONE = auto()


@dataclass(frozen=True)
class GeneratorRow:
    ordinal: int
    status: str
    assigned_users: int
    external_ref: str | None
    last_heartbeat: datetime | None


@dataclass(frozen=True)
class RunView:
    status: str
    generators: list[GeneratorRow]
    now: datetime
    duration_seconds: int
    started_at: datetime | None = None
    max_capacity_loss_percent: int = 10


def _lost_fraction(view: RunView) -> float:
    planned = sum(generator.assigned_users for generator in view.generators)
    if planned == 0:
        return 1.0
    lost = sum(
        generator.assigned_users
        for generator in view.generators
        if generator.status in (GeneratorStatus.LOST, GeneratorStatus.FAILED)
    )
    return lost / planned


def decide(view: RunView) -> Decision:
    if view.status in TERMINAL_RUN_STATUSES:
        return Decision.DONE

    if view.status == RunStatus.QUEUED:
        return Decision.PROVISION

    silent = [
        generator
        for generator in view.generators
        if generator.status not in TERMINAL_GENERATOR_STATUSES
        and generator.last_heartbeat is not None
        and view.now - generator.last_heartbeat > HEARTBEAT_TIMEOUT
    ]
    if silent:
        return Decision.MARK_LOST

    if _lost_fraction(view) > 0:
        # Capacity loss is judged as a percentage of planned virtual users, so
        # the same absolute loss means different things to different runs.
        if _lost_fraction(view) * 100 >= view.max_capacity_loss_percent:
            return Decision.FAIL
        if view.status == RunStatus.RUNNING:
            return Decision.CONTINUE_DEGRADED

    live = [
        generator
        for generator in view.generators
        if generator.status not in TERMINAL_GENERATOR_STATUSES
    ]
    if not live:
        return Decision.FINISH

    if view.status == RunStatus.STARTING and all(
        generator.status == GeneratorStatus.READY for generator in live
    ):
        return Decision.START

    return Decision.WAIT
```

`Decision.PROVISION` is returned only from `QUEUED`, and the applying code moves the run to `ALLOCATING` with a conditional update before it creates anything — so the second copy of a duplicated message finds the run already past `QUEUED` and decides `WAIT`.

- [ ] **Step 4: Write the orchestrator and the process**

`apps/worker/plimsoll_worker/__main__.py`:

```python
"""The worker process: consume, reconcile, repeat.

It ships in the API's image with a different command. One codebase (ADR-0001),
one build, and a service that scales and fails on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from plimsoll_api.db.session import session_for_org
from plimsoll_api.messaging import RUNS_EXECUTION, WORKER_GROUP, get_bus, run_channel
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import issue_agent_token
from plimsoll_contracts.agent import Command
from plimsoll_contracts.runs import RunStatus
from plimsoll_worker.reconciler import Decision, GeneratorRow, RunView, decide
from plimsoll_worker.runtime.base import GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime

TICK_SECONDS = 2
GRACE_SECONDS = 120
NETWORK = os.environ.get("PLIMSOLL_GENERATOR_NETWORK", "plimsoll_default")
INTERNAL_API_URL = os.environ.get("PLIMSOLL_INTERNAL_API_URL", "http://api:8000")


class Orchestrator:
    def __init__(self) -> None:
        self._runtime = DockerRuntime()
        self._bus = get_bus()
        self._active: dict[uuid.UUID, uuid.UUID] = {}

    async def reconcile(self, run_id: uuid.UUID, org_id: uuid.UUID) -> bool:
        """Returns whether the run is finished with. One tick, one decision."""
        async with session_for_org(org_id) as session:
            run = await repo.get(session, run_id)
            if run is None:
                # Its transaction rolled back, or it never committed. Nothing
                # to do, and nothing to keep.
                return True
            generators = await repo.generators_for(session, run_id)

        view = RunView(
            status=run.status,
            generators=[
                GeneratorRow(
                    ordinal=row.ordinal,
                    status=row.status,
                    assigned_users=row.assigned_users,
                    external_ref=row.external_ref,
                    last_heartbeat=row.last_heartbeat,
                )
                for row in generators
            ],
            now=datetime.now(UTC),
            duration_seconds=int(run.configuration_snapshot["workload"]["durationSeconds"]),
            started_at=run.started_at,
            max_capacity_loss_percent=int(
                run.configuration_snapshot["workload"].get("maxCapacityLossPercent", 10)
            ),
        )
        decision = decide(view)

        if decision is Decision.DONE:
            return True
        if decision is Decision.PROVISION:
            await self._provision(run, org_id, view)
        elif decision is Decision.START:
            await self._command(run.id, org_id, RunStatus.RUNNING, Command.START)
        elif decision is Decision.MARK_LOST:
            await self._mark_lost(run.id, org_id, view)
        elif decision is Decision.CONTINUE_DEGRADED:
            async with session_for_org(org_id) as session:
                await repo.mark_degraded(session, run.id)
        elif decision is Decision.FAIL:
            await self._finish(run, org_id, RunStatus.FAILED)
            return True
        elif decision is Decision.FINISH:
            await self._finish(run, org_id, RunStatus.COMPLETED)
            return True
        return False

    async def _provision(self, run, org_id: uuid.UUID, view: RunView) -> None:
        async with session_for_org(org_id) as session:
            moved = await repo.transition(
                session, run.id, expected=[RunStatus.QUEUED], to=RunStatus.ALLOCATING
            )
        if moved is None:
            # Another worker took it. Provisioning twice is exactly the failure
            # at-least-once delivery invites, and this is where it is refused.
            return

        image = run.configuration_snapshot.get("image") or await self._pool_image(run, org_id)
        ttl = view.duration_seconds + GRACE_SECONDS
        specs = [
            GeneratorSpec(
                run_id=run.id,
                ordinal=generator.ordinal,
                image=image,
                network=NETWORK,
                environment={
                    "PLIMSOLL_API_URL": INTERNAL_API_URL,
                    "PLIMSOLL_RUN_ID": str(run.id),
                    "PLIMSOLL_ORDINAL": str(generator.ordinal),
                    "PLIMSOLL_RUN_TOKEN": issue_agent_token(
                        run.id, ordinal=generator.ordinal, org_id=org_id, ttl_seconds=ttl
                    ),
                },
                labels={"plimsoll.run": str(run.id)},
            )
            for generator in view.generators
            if generator.external_ref is None
        ]

        handles = []
        try:
            handles = await self._runtime.provision(specs)
        except Exception:  # noqa: BLE001 - any runtime failure fails the run
            await self._runtime.teardown(handles)
            await self._finish(run, org_id, RunStatus.FAILED)
            raise
        finally:
            async with session_for_org(org_id) as session:
                for handle in handles:
                    await repo.attach_external_ref(
                        session, run.id, handle.ordinal, handle.external_ref
                    )

        async with session_for_org(org_id) as session:
            await repo.transition(
                session, run.id, expected=[RunStatus.ALLOCATING], to=RunStatus.STARTING
            )

    async def _pool_image(self, run, org_id: uuid.UUID) -> str:
        async with session_for_org(org_id) as session:
            from plimsoll_api.repositories import pools as pools_repo

            pool = await pools_repo.get(
                session, uuid.UUID(run.configuration_snapshot["workload"]["generatorPoolId"])
            )
        return str(pool.config["image"])

    async def _command(
        self, run_id: uuid.UUID, org_id: uuid.UUID, status: RunStatus, command: Command
    ) -> None:
        async with session_for_org(org_id) as session:
            await repo.transition(
                session,
                run_id,
                expected=[RunStatus.STARTING],
                to=status,
                started=status is RunStatus.RUNNING,
            )
        await self._bus.announce(run_channel(run_id), {"command": command})

    async def _mark_lost(self, run_id: uuid.UUID, org_id: uuid.UUID, view: RunView) -> None:
        async with session_for_org(org_id) as session:
            for generator in view.generators:
                if (
                    generator.last_heartbeat is not None
                    and view.now - generator.last_heartbeat > timedelta(seconds=35)
                    and generator.status not in ("COMPLETED", "FAILED", "LOST")
                ):
                    await repo.set_generator_status(
                        session, run_id, generator.ordinal, "LOST"
                    )

    async def _finish(self, run, org_id: uuid.UUID, status: RunStatus) -> None:
        handles = []
        async with session_for_org(org_id) as session:
            rows = await repo.generators_for(session, run.id)
        from plimsoll_worker.runtime.base import GeneratorHandle

        handles = [
            GeneratorHandle(ordinal=row.ordinal, external_ref=row.external_ref)
            for row in rows
            if row.external_ref
        ]
        await self._runtime.teardown(handles)

        async with session_for_org(org_id) as session:
            await repo.transition(
                session,
                run.id,
                expected=[
                    RunStatus.QUEUED,
                    RunStatus.ALLOCATING,
                    RunStatus.STARTING,
                    RunStatus.RUNNING,
                    RunStatus.STOPPING,
                ],
                to=status,
                ended=True,
            )
            await repo.set_summary(
                session,
                run.id,
                {"generators": len(handles), "outcome": str(status)},
            )


async def main() -> None:
    bus = get_bus()
    await bus.ensure_group(RUNS_EXECUTION, WORKER_GROUP)
    orchestrator = Orchestrator()
    consumer = f"worker-{socket.gethostname()}"
    tracked: dict[uuid.UUID, tuple[uuid.UUID, object]] = {}

    while True:
        deliveries = await bus.read(
            RUNS_EXECUTION, WORKER_GROUP, consumer, count=10, block_ms=1000
        )
        deliveries += await bus.reclaim_stale(
            RUNS_EXECUTION, WORKER_GROUP, consumer, idle=timedelta(seconds=60)
        )
        for delivery in deliveries:
            run_id = uuid.UUID(delivery.payload["runId"])
            org_id = uuid.UUID(delivery.payload["organizationId"])
            tracked[run_id] = (org_id, delivery)

        for run_id, (org_id, delivery) in list(tracked.items()):
            finished = await orchestrator.reconcile(run_id, org_id)
            if finished:
                await bus.acknowledge(RUNS_EXECUTION, WORKER_GROUP, delivery)  # type: ignore[arg-type]
                del tracked[run_id]

        await asyncio.sleep(TICK_SECONDS)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
```

A message is acknowledged only when its run is finished with. A worker that dies mid-run leaves it pending, and `reclaim_stale` hands it to whoever is alive — which is the whole reason the decision is computed from the database rather than remembered.

In `docker-compose.yml`, add the service:

```yaml
  worker:
    build:
      context: ../..
      dockerfile: infrastructure/docker/api.Dockerfile
    command: ["uv", "run", "python", "-m", "plimsoll_worker"]
    environment:
      PLIMSOLL_DATABASE_URL: postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@postgres:5432/plimsoll
      PLIMSOLL_MIGRATION_DATABASE_URL: postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@postgres:5432/plimsoll
      PLIMSOLL_REDIS_URL: redis://redis:6379/0
      PLIMSOLL_S3_ENDPOINT: http://minio:9000
      PLIMSOLL_JWT_SECRET: development-only-secret-change-me
      PLIMSOLL_CREDENTIAL_KEY: ZGV2ZWxvcG1lbnQtb25seS1rZXktMzItYnl0ZXMhISE=
      PLIMSOLL_ENVIRONMENT: development
      PLIMSOLL_INTERNAL_API_URL: http://api:8000
      PLIMSOLL_GENERATOR_NETWORK: plimsoll_default
    volumes:
      # Root-equivalent on the host, and mounted here and nowhere else: this is
      # the only process that creates containers.
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      minio: {condition: service_healthy}
```

The API image must now carry the worker's source: in `infrastructure/docker/api.Dockerfile`, add `COPY apps/worker/pyproject.toml apps/worker/` beside the API's, and `COPY apps/worker apps/worker` beside `COPY apps/api apps/api`.

- [ ] **Step 5: Write the end-to-end test**

`apps/api/tests/integration/test_run_execution.py`:

```python
"""The whole path, over HTTP: a run starts, containers appear, it completes."""

import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _short_test(client: httpx.Client, seconds: int = 10, users: int = 2) -> str:
    project_id = str(
        client.post(
            "/api/v1/projects",
            json={"name": "Execution", "projectKey": f"X{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
        client.post(
            f"/api/v1/projects/{project_id}/script-repos",
            json={
                "name": f"repo-{uuid.uuid4().hex[:6]}",
                "repoUrl": "http://script-fixture/public/plans.git",
                "planPath": "perf/checkout.jmx",
                "defaultRef": "main",
            },
        ).json()["id"]
    )
    pool_id = next(
        str(item["id"])
        for item in client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    return str(
        client.post(
            f"/api/v1/projects/{project_id}/tests",
            json={
                "name": "Short run",
                "configuration": {
                    "virtualUsers": users,
                    "durationSeconds": seconds,
                    "rampUpSeconds": 1,
                    "generatorPoolId": pool_id,
                },
                "plans": [
                    {"scriptRepoId": repo_id, "virtualUsers": users, "executionOrder": 1}
                ],
                "slaRules": [],
            },
        ).json()["id"]
    )


def _await_status(client: httpx.Client, run_id: str, wanted: set[str], timeout: int = 120):
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/v1/runs/{run_id}/status").json()
        if last["status"] in wanted:
            return last
        time.sleep(2)
    raise AssertionError(f"run stayed at {last.get('status')}: {last}")


def test_a_run_reaches_running_and_then_completes(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]

    running = _await_status(admin_client, run_id, {"RUNNING"})
    assert all(g["status"] in {"RUNNING", "READY"} for g in running["generators"])

    completed = _await_status(admin_client, run_id, TERMINAL)
    assert completed["status"] == "COMPLETED", completed
    assert completed["endedAt"] is not None


def test_every_generator_registers_and_heartbeats(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    running = _await_status(admin_client, run_id, {"RUNNING"})
    assert len(running["generators"]) >= 1
    assert all(g["lastHeartbeat"] is not None for g in running["generators"])
    _await_status(admin_client, run_id, TERMINAL)
```

- [ ] **Step 6: Run everything**

Run: `make dev-down && make dev && uv run pytest apps/worker/tests/unit -v && uv run pytest apps/api/tests/integration/test_run_execution.py -v -m integration`
Expected: PASS. Read the worker's log with `docker compose -f infrastructure/docker/docker-compose.yml logs worker` when a run stalls; the status endpoint tells you where it stopped and the log tells you why.

- [ ] **Step 7: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(worker): reconcile a run from queued to completed"
```

---

### Task 9: Stopping, cancelling, and losing a generator

**Files:**
- Modify: `apps/api/plimsoll_api/routers/runs.py`, `apps/api/plimsoll_api/services/runs.py`
- Test: `apps/api/tests/integration/test_run_failure.py`

**Interfaces:**
- Produces: `POST /runs/{id}/stop`, `POST /runs/{id}/cancel`, both idempotent.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_run_failure.py`:

```python
"""Stopping, and what happens when a generator disappears."""

import subprocess
import time

import httpx
import pytest

from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration


def test_stop_is_idempotent(admin_client: httpx.Client) -> None:
    """Invariant 5: repeating stop returns 200 and re-runs no side effect."""
    test_id = _short_test(admin_client, seconds=60)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    first = admin_client.post(f"/api/v1/runs/{run_id}/stop")
    second = admin_client.post(f"/api/v1/runs/{run_id}/stop")
    assert first.status_code == 200
    assert second.status_code == 200

    final = _await_status(admin_client, run_id, TERMINAL)
    assert final["status"] == "COMPLETED"


def test_stopping_a_finished_run_is_still_200(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=5)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL)
    assert admin_client.post(f"/api/v1/runs/{run_id}/stop").status_code == 200


def test_cancel_abandons_the_run(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=60)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    assert admin_client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
    final = _await_status(admin_client, run_id, TERMINAL)
    assert final["status"] == "CANCELLED"


def test_a_killed_generator_never_looks_like_success(admin_client: httpx.Client) -> None:
    """The invariant test. A run that lost capacity must say so -- a result
    produced with less load than planned must never pass as a full one."""
    test_id = _short_test(admin_client, seconds=90, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    killed = subprocess.run(  # noqa: S603
        ["docker", "ps", "-q", "--filter", f"label=plimsoll.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert killed, "no generator containers were labelled with the run"
    subprocess.run(["docker", "kill", killed[0]], check=True, capture_output=True)  # noqa: S603

    final = _await_status(admin_client, run_id, TERMINAL, timeout=180)
    assert final["status"] == "FAILED" or final["degraded"] is True, final


def test_a_viewer_cannot_stop_a_run(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    test_id = _short_test(admin_client, seconds=15)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    assert viewer_client.post(f"/api/v1/runs/{run_id}/stop").status_code == 403
    _await_status(admin_client, run_id, TERMINAL)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_run_failure.py -v -m integration`
Expected: FAIL — `404` from `/stop`.

- [ ] **Step 3: Write the endpoints**

Add to `services/runs.py`:

```python
async def request_stop(
    session: AsyncSession, principal: AccessClaims, run_id: uuid.UUID, *, cancel: bool
) -> Any:
    """Idempotent by construction: a state write and an announcement.

    A run already terminal is left alone and answered 200 -- repeating stop on
    a stopped run must not re-run a side effect (invariant 5).
    """
    run = await require(session, run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return run

    # Before RUNNING there is no load to wind down, so a stop is a cancel.
    ending = RunStatus.CANCELLED if cancel or run.status != RunStatus.RUNNING else None
    if ending is not None:
        moved = await repo.transition(
            session,
            run_id,
            expected=[RunStatus.QUEUED, RunStatus.ALLOCATING, RunStatus.STARTING,
                      RunStatus.RUNNING],
            to=RunStatus.CANCELLED,
            ended=True,
        )
    else:
        moved = await repo.transition(
            session, run_id, expected=[RunStatus.RUNNING], to=RunStatus.STOPPING
        )
    if moved is None:
        # Someone got there first. Their transition stands; ours never happened.
        return await require(session, run_id)

    await audit.record(
        session,
        principal=principal,
        action="run.cancelled" if cancel else "run.stopped",
        entity_type="test_run",
        entity_id=run_id,
        metadata={"from": run.status},
    )
    return moved
```

Import `RunStatus` and `TERMINAL_RUN_STATUSES` from `plimsoll_contracts.runs`.

Add to `routers/runs.py`:

```python
@router.post(
    "/api/v1/runs/{run_id}/stop",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def stop_run(run_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    async with session_for_org(principal.organization_id) as session:
        row = await service.request_stop(session, principal, run_id, cancel=False)
    await get_bus().announce(run_channel(run_id), {"command": "STOP"})
    return _response(row)


@router.post(
    "/api/v1/runs/{run_id}/cancel",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def cancel_run(run_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    async with session_for_org(principal.organization_id) as session:
        row = await service.request_stop(session, principal, run_id, cancel=True)
    await get_bus().announce(run_channel(run_id), {"command": "CANCEL"})
    return _response(row)
```

Import `run_channel` from `plimsoll_api.messaging`.

In the worker, a `STOPPING` run whose generators have all gone terminal is already handled by `Decision.FINISH`; make `_finish` preserve a cancel by passing `RunStatus.CANCELLED` when the run's current status is `CANCELLED` — the conditional update in `_finish` will simply find no row to move, which is the correct outcome, so no change is needed. Verify this with the cancel test rather than assuming it.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_run_failure.py -v -m integration`
Expected: PASS — five tests. `test_a_killed_generator_never_looks_like_success` is slow by nature: it waits out a heartbeat timeout. If it reports `COMPLETED` with `degraded` false, capacity loss is not being computed — check that the killed generator reaches `LOST` in `GET /runs/{id}/status`.

- [ ] **Step 5: Commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): stop and cancel a run, idempotently"
```

---

### Task 10: Pool connectivity, documentation, and the slice demonstration

**Files:**
- Create: `apps/api/tests/integration/test_slice3a_demonstration.py`
- Modify: `apps/api/plimsoll_api/routers/pools.py`, `apps/api/plimsoll_api/services/pools.py`, `docs/architecture/06-api.md`, `docs/architecture/02-execution-plane.md`, `README.md`
- Test: `apps/api/tests/integration/test_slice3a_demonstration.py`

- [ ] **Step 1: Write `test-connection`**

S2 deferred this endpoint because it needed a runtime. The runtime now exists —
but it lives in the **worker**, which is the only process holding the Docker
socket. The API cannot answer this question itself, and giving it a socket to
answer a diagnostic would hand root-equivalent host access to the process that
serves untrusted requests.

So the API asks the worker, over the bus it already has, and waits briefly for
the reply. Add a second stream and a reply channel in `messaging.py`:

```python
POOL_PROBES = "pools.probe"


def probe_channel(probe_id: Any) -> str:
    return f"pools:probe:{probe_id}"
```

`services/pools.py`:

```python
PROBE_TIMEOUT_SECONDS = 5


async def test_connection(session: AsyncSession, pool_id: uuid.UUID) -> ProbeResult:
    """Can this pool actually create generators, and is its image present?

    Answered by the worker, because the worker is the only process that holds a
    container runtime. Reported rather than raised: an operator fixing a pool
    wants the answer, not an exception.
    """
    pool = await require(session, pool_id)
    probe_id = uuid.uuid4()
    bus = get_bus()

    async with bus.listen(probe_channel(probe_id)) as replies:
        await bus.publish(
            POOL_PROBES,
            {
                "probeId": str(probe_id),
                "runtime": pool.runtime,
                "image": str(pool.config.get("image", "")),
            },
        )
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                async for reply in replies:
                    return ProbeResult(ok=reply["ok"] == "true", detail=reply["detail"])
        except TimeoutError:
            pass

    return ProbeResult(
        ok=False,
        detail="No worker answered. The execution plane may be down.",
    )
```

with `ProbeResult` in `plimsoll_contracts/pools.py`:

```python
class ProbeResult(BaseModel):
    ok: bool
    detail: str
```

Add to `DockerRuntime` the probe itself:

```python
    async def check(self, image: str) -> tuple[bool, str]:
        def probe() -> tuple[bool, str]:
            try:
                self._client.ping()
            except DockerException as exc:
                return False, f"The container runtime is unreachable: {exc}"
            try:
                self._client.images.get(image)
            except ImageNotFound:
                return False, f"The image {image} is not present on the runtime."
            except DockerException as exc:
                return False, f"The image could not be inspected: {exc}"
            return True, f"The runtime is reachable and {image} is present."

        return await asyncio.to_thread(probe)
```

importing `ImageNotFound` from `docker.errors`. In the worker's `main` loop, read
`POOL_PROBES` alongside `RUNS_EXECUTION` and answer each one:

```python
        for delivery in await bus.read(POOL_PROBES, WORKER_GROUP, consumer, count=5, block_ms=0):
            if delivery.payload["runtime"] != "docker":
                ok, detail = False, f"No runtime is implemented for {delivery.payload['runtime']}."
            else:
                ok, detail = await orchestrator.check_image(delivery.payload["image"])
            await bus.announce(
                probe_channel(delivery.payload["probeId"]),
                {"ok": "true" if ok else "false", "detail": detail},
            )
            await bus.acknowledge(POOL_PROBES, WORKER_GROUP, delivery)
```

with `Orchestrator.check_image` delegating to `self._runtime.check(image)`, and
`await bus.ensure_group(POOL_PROBES, WORKER_GROUP)` beside the existing one at
startup. The router endpoint takes `Permission.ADMIN_SYSTEM`, matching every
other pool write:

```python
@router.post(
    "/{pool_id}/test-connection",
    response_model=ProbeResult,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def test_pool_connection(pool_id: uuid.UUID, session: TenantSession) -> ProbeResult:
    return await service.test_connection(session, pool_id)
```

Test it in `test_slice3a_demonstration.py`:

```python
def test_the_seeded_pool_reports_a_working_runtime(admin_client: httpx.Client) -> None:
    pool_id = next(
        str(item["id"])
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    body = admin_client.post(f"/api/v1/generator-pools/{pool_id}/test-connection").json()
    assert body["ok"] is True, body["detail"]


def test_a_viewer_cannot_probe_a_pool(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    pool_id = next(
        str(item["id"])
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    assert (
        viewer_client.post(f"/api/v1/generator-pools/{pool_id}/test-connection").status_code
        == 403
    )
```

- [ ] **Step 2: Write the demonstration test**

`apps/api/tests/integration/test_slice3a_demonstration.py`:

```python
"""The S3a promise: a defined test becomes containers, and comes back clean."""

import httpx
import pytest

from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration


def test_a_test_becomes_a_run_and_returns(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10, users=2)

    created = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()
    assert created["status"] == "QUEUED"
    assert len(created["configurationSnapshot"]["plans"][0]["commitSha"]) == 40

    running = _await_status(admin_client, created["id"], {"RUNNING"})
    assert running["generators"], "a running run has generators"

    final = _await_status(admin_client, created["id"], TERMINAL)
    assert final["status"] == "COMPLETED"
    assert final["degraded"] is False

    detail = admin_client.get(f"/api/v1/runs/{created['id']}").json()
    assert detail["summary"]["generators"] >= 1
```

- [ ] **Step 3: Correct the documents**

In `docs/architecture/02-execution-plane.md`:
- Replace the run state machine's opening states — `DRAFT`, `READY`, `SCHEDULED` belong to a test, not a run. The run begins at `QUEUED`, as `06-api.md` already documents.
- Note beside the agent's steps that plan retrieval is by staged bundle, with the reason: a clone per generator would put the repository credential inside every container running a user-supplied plan. (The bundle itself lands in S3b; the document is corrected once, here.)

In `docs/architecture/06-api.md`, add the run endpoints that now exist, marking `POST /tests/{id}/runs` as returning `422 TEST_NOT_RUNNABLE` with every failing check, and `stop`/`cancel` as idempotent.

In `README.md`, extend the quickstart journey with starting a run and polling its status, using the same `curl` shape the S2 section uses.

- [ ] **Step 4: Run everything**

```bash
make dev-down && make dev
make lint && make typecheck && make test && make test-int
make contracts && git diff --quiet
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "feat(api): pool connectivity, and the S3a journey documented"
```

---

## Slice acceptance

- [ ] A test defined in S2 starts a run over HTTP and the run reaches `COMPLETED`
- [ ] `configuration_snapshot` pins the commit SHAs preflight resolved
- [ ] Generators are real containers, created with no restart policy, and are gone afterwards
- [ ] `stop` and `cancel` are idempotent, and both are `200` on an already-finished run
- [ ] Killing a generator mid-run produces a degraded or failed run, never a quiet success
- [ ] A duplicate execution message produces N containers, not 2N
- [ ] A run cannot start unless preflight passes, and the failure lists every failing check
- [ ] A `VIEWER` is refused run creation, stop, and cancel
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`, and `make contracts` all pass, the last leaving the tree clean

## Self-review notes

- **The duplicate-delivery test** named in the design's testing section is covered by `test_a_run_whose_generators_exist_is_not_provisioned_again` at the unit level and by the conditional `QUEUED → ALLOCATING` transition at the integration level. If you want the end-to-end version, publish the same message twice with `redis-cli XADD` and assert the container count; it is worth the ten lines.
- **Task 3 depends on Task 4** for `allocate`, so Task 4 is executed first. The plan is ordered for reading, not for a strict dependency walk.
- **`test-connection` goes through the bus** rather than giving the API a Docker socket. Handing root-equivalent host access to the process that serves untrusted requests, in order to answer a diagnostic, is a bad trade. The cost is a request/reply over Redis with a short timeout, and a clear answer when no worker replies.
- **The design's `summary` is minimal in S3a** — generator count and outcome. S3b adds artifact keys and warnings; S4 adds the numbers. Nothing reads it yet, which is why it stays small rather than being designed ahead of a consumer.
