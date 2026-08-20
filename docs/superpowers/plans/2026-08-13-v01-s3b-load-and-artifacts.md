# Plimsoll v0.1 Slice 3b — Load and Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The run that S3a orchestrates now generates real load — JMeter executes the pinned plan against the demo target — and its raw artifacts land in object storage where the API can hand them back.

**Architecture:** The worker fetches the plan once at the pinned commit and stages it as a bundle in object storage; agents download it by presigned URL, verify its digest, inject variables in memory, re-check the target against the policy, and run JMeter through the `Executor` seam. Artifacts go back the same way they came: presigned `PUT`s the control plane signs, so no object-store credential and no Git credential ever sits inside a container running a user-supplied plan.

**Tech Stack:** Python 3.12, JMeter 5.6.3 on a JRE base image, `boto3` for signing, `httpx` for transfers, pytest.

Design: [`docs/superpowers/specs/2026-08-13-v01-s3-execution-design.md`](../specs/2026-08-13-v01-s3-execution-design.md).
Prerequisite: [S3a](2026-08-13-v01-s3a-orchestration.md) — runs, the worker, the agent channel, `DockerRuntime`.

## Global Constraints

- Everything in S3a's Global Constraints continues to apply — no migration, `sa.text()` with bind parameters, a permission guard and an audit row on every write, conditional status transitions, `make contracts` before any commit that changes the API surface.
- **The agent never holds a durable credential.** No Git credential, no object-store key. It receives presigned URLs over its authenticated channel and nothing else. A review that finds an access key in `apps/agent` or in a generator's environment rejects the change.
- **Secrets are injected in memory and never written to disk** — not into the plan, not into a properties file, not into a log.
- **The plan file is never rewritten.** Workload parameters are JMeter properties ([ADR-0006](../../adr/0006-jmeter-first-executor.md)). A plan that runs here and in the tester's local JMeter must be the same bytes.
- **Nothing is downloaded from the internet at run time.** Plugins come from the image; the plan comes from the bundle. An air-gapped install is a stated v1.0 goal and is cheap to keep, expensive to recover.
- **The target policy is re-checked by the agent** immediately before traffic starts ([invariant 8](../../../CLAUDE.md)). The check rejects; it never warns.

## File Structure

```
packages/executor-sdk/
  pyproject.toml
  plimsoll_executor/__init__.py base.py jmeter.py
apps/api/plimsoll_api/
  storage.py                       Bucket, keys, presigned URLs
  routers/agent.py                 (modified) artifact_url_request
  routers/runs.py                  (modified) artifact list and download
apps/worker/plimsoll_worker/
  bundle.py                        Fetch at the pinned SHA, pack, upload
  __main__.py                      (modified) stage before provisioning
apps/agent/plimsoll_agent/
  bundle.py                        Download, verify, unpack
  targets.py                       The second target-policy check
  execution.py                     Run JMeter, upload what it produced
infrastructure/docker/
  generator.Dockerfile             (modified) JRE + JMeter
apps/api/tests/integration/
  test_storage.py test_artifacts_api.py test_slice3_demonstration.py
apps/worker/tests/integration/test_bundle.py
apps/agent/tests/unit/test_targets.py
packages/executor-sdk/tests/test_jmeter.py
```

---

### Task 1: The executor seam

**Files:**
- Create: `packages/executor-sdk/pyproject.toml`, `packages/executor-sdk/plimsoll_executor/__init__.py`, `packages/executor-sdk/plimsoll_executor/base.py`, `packages/executor-sdk/plimsoll_executor/jmeter.py`, `packages/executor-sdk/tests/__init__.py`, `packages/executor-sdk/tests/test_jmeter.py`
- Modify: `pyproject.toml` (workspace member, testpaths, first-party), `Makefile`, `apps/agent/pyproject.toml`
- Test: `packages/executor-sdk/tests/test_jmeter.py`

**Interfaces:**
- Produces: `ExecutionContext`, `Outcome`, `Executor` protocol, `JMeterExecutor` with `command`, `artifacts`, `interpret`.

- [x] **Step 1: Write the failing test**

`packages/executor-sdk/tests/test_jmeter.py`:

```python
"""The JMeter invocation, as ADR-0006 fixes it.

Pure string building, and worth testing precisely: a wrong property name does
not fail, it silently runs the plan's own defaults instead of the workload the
operator asked for.
"""

from pathlib import Path

from plimsoll_executor.base import ExecutionContext, Outcome
from plimsoll_executor.jmeter import JMeterExecutor

CONTEXT = ExecutionContext(
    plan_path=Path("/work/perf/checkout.jmx"),
    working_directory=Path("/work/perf"),
    output_directory=Path("/out"),
    threads=250,
    ramp_up_seconds=60,
    duration_seconds=600,
    variables={"API_HOST": "demo-target", "API_TOKEN": "secret-value"},
)


def test_the_command_is_headless_and_names_the_plan() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert command[0].endswith("jmeter")
    assert "-n" in command
    assert "-t" in command
    assert command[command.index("-t") + 1] == "/work/perf/checkout.jmx"


def test_the_workload_travels_as_properties() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert "-Jthreads=250" in command
    assert "-Jrampup=60" in command
    assert "-Jduration=600" in command


def test_variables_travel_as_properties_too() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert "-JAPI_HOST=demo-target" in command


def test_the_jtl_is_written_where_the_agent_expects_it() -> None:
    command = JMeterExecutor().command(CONTEXT)
    assert command[command.index("-l") + 1] == "/out/results.jtl"


def test_the_plan_is_never_rewritten() -> None:
    """No flag here edits the plan; a plan that runs on the platform and a plan
    that runs on a tester's laptop must be the same bytes."""
    command = JMeterExecutor().command(CONTEXT)
    assert not any(flag in command for flag in ("-J-", "--forceDeleteResultFile"))
    assert command.count("-t") == 1


def test_the_artifacts_are_the_jtl_and_the_log() -> None:
    names = [path.name for path in JMeterExecutor().artifacts(CONTEXT)]
    assert names == ["results.jtl", "jmeter.log"]


def test_a_clean_exit_is_a_completed_run() -> None:
    assert JMeterExecutor().interpret(0) is Outcome.COMPLETED


def test_a_non_zero_exit_is_a_failed_generator() -> None:
    """Sampler errors inside a healthy run are results. This is JMeter itself
    failing -- a broken plan, a missing plugin, a bad property."""
    assert JMeterExecutor().interpret(1) is Outcome.FAILED
    assert JMeterExecutor().interpret(255) is Outcome.FAILED
```

- [x] **Step 2: Create the package and run the test**

`packages/executor-sdk/pyproject.toml`:

```toml
[project]
name = "plimsoll-executor-sdk"
version = "0.1.0"
requires-python = ">=3.12"
# No dependencies at all. The seam has to be light enough that a second
# executor is a package, not a project.
dependencies = []

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["plimsoll_executor"]
```

Add `"packages/executor-sdk"` to the workspace members, `plimsoll-executor-sdk = { workspace = true }` to `[tool.uv.sources]`, `"plimsoll-executor-sdk"` to the root dependencies, `"plimsoll_executor"` to `known-first-party`, and `"packages/executor-sdk/tests"` to `testpaths`. Add `"plimsoll-executor-sdk"` to `apps/agent/pyproject.toml` dependencies. Extend `make test` with `packages/executor-sdk/tests`.

Run: `uv sync && uv run pytest packages/executor-sdk/tests -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_executor.base`.

- [x] **Step 3: Write the seam and the executor**

`packages/executor-sdk/plimsoll_executor/base.py`:

```python
"""The executor interface.

ADR-0006 is explicit that this exists from day one but is validated with one
real implementation rather than designed against a hypothetical second. Keep it
that way: nothing here should be shaped by a k6 that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Protocol


class Outcome(Enum):
    COMPLETED = auto()
    FAILED = auto()


@dataclass(frozen=True)
class ExecutionContext:
    plan_path: Path
    working_directory: Path
    output_directory: Path
    threads: int
    ramp_up_seconds: int
    duration_seconds: int
    # Resolved values, held in memory only. They reach the engine as process
    # arguments inside the generator and are never written to disk.
    variables: dict[str, str] = field(default_factory=dict)


class Executor(Protocol):
    def command(self, context: ExecutionContext) -> list[str]: ...

    def artifacts(self, context: ExecutionContext) -> list[Path]: ...

    def interpret(self, exit_code: int) -> Outcome: ...
```

`packages/executor-sdk/plimsoll_executor/jmeter.py`:

```python
"""Apache JMeter, driven headless, one process per generator."""

from __future__ import annotations

import os
from pathlib import Path

from plimsoll_executor.base import ExecutionContext, Executor, Outcome

JMETER_BINARY = os.environ.get("PLIMSOLL_JMETER_BINARY", "/opt/jmeter/bin/jmeter")
RESULTS_NAME = "results.jtl"
LOG_NAME = "jmeter.log"


class JMeterExecutor(Executor):
    def command(self, context: ExecutionContext) -> list[str]:
        command = [
            JMETER_BINARY,
            "-n",
            "-t",
            str(context.plan_path),
            "-l",
            str(context.output_directory / RESULTS_NAME),
            "-j",
            str(context.output_directory / LOG_NAME),
            f"-Jthreads={context.threads}",
            f"-Jrampup={context.ramp_up_seconds}",
            f"-Jduration={context.duration_seconds}",
        ]
        # Variables become properties for the same reason the workload does:
        # the plan reads ${NAME}, and rewriting the plan to bake a value in
        # would make the file unreproducible anywhere else.
        command += [f"-J{name}={value}" for name, value in sorted(context.variables.items())]
        return command

    def artifacts(self, context: ExecutionContext) -> list[Path]:
        return [
            context.output_directory / RESULTS_NAME,
            context.output_directory / LOG_NAME,
        ]

    def interpret(self, exit_code: int) -> Outcome:
        return Outcome.COMPLETED if exit_code == 0 else Outcome.FAILED
```

- [x] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest packages/executor-sdk/tests -v`
Expected: PASS — eight tests.

- [x] **Step 5: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(executor): the executor seam, with JMeter behind it"
```

---

### Task 2: Object storage

**Files:**
- Create: `apps/api/plimsoll_api/storage.py`, `apps/api/tests/integration/test_storage.py`
- Modify: `apps/api/pyproject.toml` (`boto3`), `apps/api/plimsoll_api/config.py`, `infrastructure/docker/docker-compose.yml`
- Test: `apps/api/tests/integration/test_storage.py`

**Interfaces:**
- Produces: `bundle_key(run_id)`, `artifact_key(run_id, ordinal, name)`, `ensure_bucket()`, `presign_put(key, seconds)`, `presign_get(key, seconds)`, `put_bytes(key, payload)`, `list_prefix(prefix)`.

- [x] **Step 1: Write the failing test**

`apps/api/tests/integration/test_storage.py`:

```python
"""Object storage, against the MinIO the stack already runs."""

import uuid

import httpx
import pytest

from plimsoll_api import storage

pytestmark = pytest.mark.integration


def test_the_bucket_is_created_idempotently() -> None:
    storage.ensure_bucket()
    storage.ensure_bucket()


def test_a_presigned_put_accepts_exactly_that_key() -> None:
    storage.ensure_bucket()
    run_id = uuid.uuid4()
    key = storage.artifact_key(run_id, 0, "results.jtl")

    url = storage.presign_put(key, seconds=60)
    response = httpx.put(url, content=b"sample,data\n")
    assert response.status_code in (200, 204), response.text

    assert [entry["name"] for entry in storage.list_prefix(f"runs/{run_id}/")] == [
        "results.jtl"
    ]


def test_a_presigned_get_returns_what_was_put() -> None:
    storage.ensure_bucket()
    key = storage.artifact_key(uuid.uuid4(), 0, "jmeter.log")
    storage.put_bytes(key, b"hello")

    body = httpx.get(storage.presign_get(key, seconds=60)).content
    assert body == b"hello"


def test_an_expired_url_is_refused() -> None:
    """The signature is the whole of the authorisation, so its lifetime is the
    whole of the exposure."""
    import time

    storage.ensure_bucket()
    key = storage.artifact_key(uuid.uuid4(), 0, "results.jtl")
    storage.put_bytes(key, b"x")
    url = storage.presign_get(key, seconds=1)
    time.sleep(2)
    assert httpx.get(url).status_code in (403, 400)


def test_keys_are_scoped_to_their_run_and_ordinal() -> None:
    run_id = uuid.uuid4()
    assert storage.artifact_key(run_id, 3, "results.jtl") == (
        f"runs/{run_id}/generators/3/results.jtl"
    )
    assert storage.bundle_key(run_id) == f"runs/{run_id}/bundle.tar.gz"


def test_a_name_cannot_escape_its_prefix() -> None:
    """The agent names the artifact; the control plane decides where it lands."""
    with pytest.raises(ValueError):
        storage.artifact_key(uuid.uuid4(), 0, "../../etc/passwd")
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_storage.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.storage`.

- [x] **Step 3: Configure the bucket and credentials**

Add to `apps/api/plimsoll_api/config.py`:

```python
    s3_access_key: str = "plimsoll"
    s3_secret_key: str = "plimsoll_dev_secret"
    s3_bucket: str = "plimsoll-artifacts"
    s3_region: str = "us-east-1"
```

Add the matching environment to both the `api` and `worker` services in `docker-compose.yml`:

```yaml
      PLIMSOLL_S3_ACCESS_KEY: plimsoll
      PLIMSOLL_S3_SECRET_KEY: plimsoll_dev_secret
      PLIMSOLL_S3_BUCKET: plimsoll-artifacts
```

The defaults exist so a unit test can build the application without them; the
compose values are the same because this is the development MinIO. A deployment
sets all three.

Add `"boto3>=1.34"` and `"types-boto3>=1.0"` (the latter in the root dev group) to `apps/api/pyproject.toml`, then `uv sync`.

- [x] **Step 4: Write the storage module**

`apps/api/plimsoll_api/storage.py`:

```python
"""Object storage, and the presigned URLs that keep credentials out of
generators.

A generator runs a user-supplied plan. Handing it an object-store key would put
a durable credential inside that blast radius, so instead the control plane
signs exactly the key it is willing to accept, for exactly as long as it needs
to be accepted.
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from plimsoll_api.config import get_settings

# One path segment, no traversal, no separators. The agent chooses the name;
# this is what stops the name choosing the location.
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

BUNDLE_TTL_SECONDS = 3600
ARTIFACT_PUT_TTL_SECONDS = 3600
ARTIFACT_GET_TTL_SECONDS = 300


@lru_cache
def _client() -> Any:
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        # Path addressing: MinIO does not serve virtual-host buckets by default,
        # and a signature computed for the wrong style fails at use rather than
        # at signing, which is a miserable thing to debug.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def bundle_key(run_id: uuid.UUID) -> str:
    return f"runs/{run_id}/bundle.tar.gz"


def artifact_key(run_id: uuid.UUID, ordinal: int, name: str) -> str:
    if not SAFE_NAME.match(name):
        raise ValueError(f"{name!r} is not a valid artifact name.")
    return f"runs/{run_id}/generators/{ordinal}/{name}"


def ensure_bucket() -> None:
    bucket = get_settings().s3_bucket
    try:
        _client().head_bucket(Bucket=bucket)
    except ClientError:
        # Racing another process to create it is fine; the loser sees it exists.
        try:
            _client().create_bucket(Bucket=bucket)
        except ClientError:
            pass


def presign_put(key: str, *, seconds: int = ARTIFACT_PUT_TTL_SECONDS) -> str:
    url: str = _client().generate_presigned_url(
        "put_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=seconds,
    )
    return url


def presign_get(key: str, *, seconds: int = ARTIFACT_GET_TTL_SECONDS) -> str:
    url: str = _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": get_settings().s3_bucket, "Key": key},
        ExpiresIn=seconds,
    )
    return url


def put_bytes(key: str, payload: bytes) -> None:
    _client().put_object(Bucket=get_settings().s3_bucket, Key=key, Body=payload)


def list_prefix(prefix: str) -> list[dict[str, Any]]:
    response = _client().list_objects_v2(Bucket=get_settings().s3_bucket, Prefix=prefix)
    return [
        {
            "key": item["Key"],
            "name": item["Key"].rsplit("/", 1)[-1],
            "size": item["Size"],
            "lastModified": item["LastModified"],
        }
        for item in response.get("Contents", [])
    ]
```

The presigned URLs are signed against the endpoint the *signer* knows, which
inside compose is `http://minio:9000`. A test running on the host cannot resolve
that name. Add a host-visible endpoint for signing when one is configured:

```python
    s3_public_endpoint: str = ""
```

in `Settings`, defaulting to empty, and in `_client()` use
`settings.s3_public_endpoint or settings.s3_endpoint`. Set
`PLIMSOLL_S3_PUBLIC_ENDPOINT: http://localhost:9000` on the `api` and `worker`
services, because `make dev` publishes MinIO on that port and every consumer of
a presigned URL — the agent in a container, a browser, a test on the host —
reaches it there. Uploads and downloads made by the control plane itself
continue through `s3_endpoint`.

Wait — a generator container cannot reach `localhost:9000`. Use a single name
that resolves in both places instead: add `minio` to the compose network aliases
and set `PLIMSOLL_S3_PUBLIC_ENDPOINT: http://localhost:9000` only for tests, via
`PLIMSOLL_TEST_S3_URL` in the integration conftest, rewriting the host of a
presigned URL before use:

```python
def _reachable(url: str) -> str:
    """A URL signed for the compose network, rewritten for the host that runs
    the tests. The signature covers the path and query, not the host."""
    return url.replace("http://minio:9000", "http://localhost:9000")
```

Put `_reachable` in `apps/api/tests/integration/conftest.py` and use it in every
test that fetches a presigned URL. Signing stays on one endpoint, which is the
property that matters.

- [x] **Step 5: Create the bucket at startup**

In `apps/worker/plimsoll_worker/__main__.py`, call `ensure_bucket()` once before the loop:

```python
async def main() -> None:
    ensure_bucket()
    bus = get_bus()
```

so `make dev` needs no extra step and a deployment needs no manual one.

- [x] **Step 6: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_storage.py -v -m integration`
Expected: PASS — six tests. Wrap the URLs in `_reachable` where the tests call `httpx`.

- [x] **Step 7: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): object storage, reached by presigned URL"
```

---

### Task 3: The plan bundle

**Files:**
- Create: `apps/worker/plimsoll_worker/bundle.py`, `apps/worker/tests/integration/test_bundle.py`
- Modify: `apps/worker/plimsoll_worker/__main__.py`
- Test: `apps/worker/tests/integration/test_bundle.py`

**Interfaces:**
- Produces: `stage(run_id, plans, access_for) -> BundleRef(key, sha256)`; the worker stages before it provisions, and records the reference in the run's summary.

- [x] **Step 1: Write the failing test**

`apps/worker/tests/integration/test_bundle.py`:

```python
"""Staging the plan once, centrally, instead of cloning per generator."""

import hashlib
import io
import tarfile
import uuid

import httpx
import pytest

from plimsoll_api import storage
from plimsoll_api.git.client import GitAccess
from plimsoll_worker.bundle import stage

pytestmark = pytest.mark.integration

PUBLIC = "http://localhost:8081/public/plans.git"


async def _sha_of_main() -> str:
    from plimsoll_api.git.client import resolve_ref

    return (await resolve_ref(GitAccess(PUBLIC), "main")).sha


async def test_the_bundle_carries_the_plan_and_everything_beside_it() -> None:
    storage.ensure_bucket()
    run_id = uuid.uuid4()
    reference = await stage(
        run_id,
        [
            {
                "access": GitAccess(PUBLIC),
                "commitSha": await _sha_of_main(),
                "planPath": "perf/checkout.jmx",
            }
        ],
    )

    url = storage.presign_get(reference.key, seconds=60).replace(
        "http://minio:9000", "http://localhost:9000"
    )
    payload = httpx.get(url).content
    assert hashlib.sha256(payload).hexdigest() == reference.sha256

    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        names = archive.getnames()
    assert "perf/checkout.jmx" in names
    assert "perf/plimsoll.yaml" in names
    assert "perf/data/users.csv" in names


async def test_staging_the_same_commit_twice_gives_the_same_digest() -> None:
    """Byte-identical inputs are a checksum, not an assumption."""
    storage.ensure_bucket()
    sha = await _sha_of_main()
    plans = [{"access": GitAccess(PUBLIC), "commitSha": sha, "planPath": "perf/checkout.jmx"}]

    first = await stage(uuid.uuid4(), plans)
    second = await stage(uuid.uuid4(), plans)
    assert first.sha256 == second.sha256


async def test_an_unreachable_repository_raises() -> None:
    from plimsoll_api.git.client import GitError

    with pytest.raises(GitError):
        await stage(
            uuid.uuid4(),
            [
                {
                    "access": GitAccess("http://nowhere.invalid/r.git"),
                    "commitSha": "0" * 40,
                    "planPath": "perf/checkout.jmx",
                }
            ],
        )
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/worker/tests/integration/test_bundle.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_worker.bundle`.

- [x] **Step 3: Write the bundler**

`apps/worker/plimsoll_worker/bundle.py`:

```python
"""One fetch per run, packed and staged for every generator.

The alternative -- a clone per generator -- would put the customer's Git
credential inside every container running a user-supplied plan, and would need
git and credential handling in the generator image. Staging centrally keeps the
credential in the control plane and makes "every generator ran the same bytes"
a digest rather than a hope.
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plimsoll_api import storage
from plimsoll_api.git.client import fetch_plan

# Fixed metadata, so the same tree packs to the same bytes on every worker and
# the digest means what it says.
EPOCH = 0


@dataclass(frozen=True)
class BundleRef:
    key: str
    sha256: str


def _reset(entry: tarfile.TarInfo) -> tarfile.TarInfo:
    entry.uid = entry.gid = 0
    entry.uname = entry.gname = ""
    entry.mtime = EPOCH
    return entry


def _pack(roots: list[tuple[Path, str]]) -> bytes:
    buffer = io.BytesIO()
    # gzip with mtime=0 for the same reason: a timestamp in the header would
    # make two identical trees produce two different digests.
    with tarfile.open(fileobj=buffer, mode="w:gz", compresslevel=6) as archive:
        archive.gzip_mtime = EPOCH  # type: ignore[attr-defined]
        for root, prefix in roots:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.add(
                        path, arcname=f"{prefix}/{path.relative_to(root)}", filter=_reset
                    )
    return buffer.getvalue()


async def stage(run_id: uuid.UUID, plans: list[dict[str, Any]]) -> BundleRef:
    """Fetch each plan at its pinned commit and pack the directory it lives in.

    The plan's own directory is what travels, because everything a plan
    references -- CSV data, .properties, the manifest -- resolves by relative
    path from the plan file.
    """
    payloads: list[bytes] = []
    for plan in plans:
        plan_path = str(plan["planPath"])
        directory = Path(plan_path).parent.as_posix()
        async with fetch_plan(plan["access"], str(plan["commitSha"]), plan_path) as root:
            payloads.append(_pack([(root / directory, directory)]))

    # v0.1 runs one plan per test; when that changes, the packs merge here
    # rather than at the call site.
    payload = payloads[0]
    digest = hashlib.sha256(payload).hexdigest()
    key = storage.bundle_key(run_id)
    storage.put_bytes(key, payload)
    return BundleRef(key=key, sha256=digest)
```

- [x] **Step 4: Stage before provisioning**

In `apps/worker/plimsoll_worker/__main__.py`, `_provision` stages the bundle after it wins the `QUEUED → ALLOCATING` transition and before it creates any container, and passes the reference to each agent through the environment:

```python
        bundle = await self._stage(run, org_id)
        ...
                environment={
                    "PLIMSOLL_API_URL": INTERNAL_API_URL,
                    "PLIMSOLL_RUN_ID": str(run.id),
                    "PLIMSOLL_ORDINAL": str(generator.ordinal),
                    "PLIMSOLL_RUN_TOKEN": issue_agent_token(...),
                    "PLIMSOLL_BUNDLE_URL": bundle_url,
                    "PLIMSOLL_BUNDLE_SHA256": bundle.sha256,
                },
```

with:

```python
    async def _stage(self, run, org_id: uuid.UUID) -> BundleRef:
        """Git access is assembled inside a transaction; the fetch happens
        outside one."""
        snapshot = run.configuration_snapshot
        async with session_for_org(org_id) as session:
            plans = []
            for plan in snapshot["plans"]:
                repo_row = await script_repos.require(
                    session, uuid.UUID(plan["scriptRepoId"])
                )
                plans.append(
                    {
                        "access": await script_repos.access_for(session, repo_row),
                        "commitSha": plan["commitSha"],
                        "planPath": plan["planPath"],
                    }
                )
        return await stage(run.id, plans)
```

and `bundle_url = storage.presign_get(bundle.key, seconds=view.duration_seconds + GRACE_SECONDS)`.
Import `script_repos` from `plimsoll_api.services`, `stage` and `BundleRef` from `plimsoll_worker.bundle`, and `storage` from `plimsoll_api`.

A staging failure fails the run: wrap the call in the same `try` that already
fails the run on a provisioning error, so an unreachable repository produces
`FAILED` with a reason rather than containers with nothing to run.

- [x] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/worker/tests/integration/test_bundle.py -v -m integration`
Expected: PASS — three tests. If `archive.gzip_mtime` is rejected by this Python's tarfile, drop that line and instead build the gzip layer with `gzip.GzipFile(fileobj=..., mtime=0)` around a `w` mode tar; the determinism test is what tells you which you need.

- [x] **Step 6: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(worker): stage the plan once, for every generator"
```

---

### Task 4: JMeter in the image

**Files:**
- Modify: `infrastructure/docker/generator.Dockerfile`
- Test: `apps/worker/tests/integration/test_docker_runtime.py` (extended)

**Interfaces:**
- Produces: `ghcr.io/ultron13/generator:dev` carrying a JRE, JMeter 5.6.3 at `/opt/jmeter`, and the agent.

- [ ] **Step 1: Write the failing test**

Append to `apps/worker/tests/integration/test_docker_runtime.py`:

```python
def test_the_generator_image_carries_jmeter() -> None:
    """The version is pinned in the image, not resolved at run time: an
    air-gapped install is a stated goal, and a run must never depend on a
    download."""
    result = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--entrypoint", "/opt/jmeter/bin/jmeter", IMAGE, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "5.6.3" in result.stdout + result.stderr


def test_the_generator_image_has_no_git() -> None:
    """Plans arrive as a staged bundle. A git binary here would invite the
    credential that staging exists to avoid."""
    result = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--entrypoint", "sh", IMAGE, "-c", "command -v git"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, f"git is present at {result.stdout.strip()}"
```

with `import subprocess` at the top.

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/worker/tests/integration/test_docker_runtime.py -v -m integration -k jmeter`
Expected: FAIL — there is no `/opt/jmeter`.

- [ ] **Step 3: Add JMeter to the image**

`infrastructure/docker/generator.Dockerfile` gains a download stage and a JRE base. The checksum is not decoration: it is the only thing standing between a mirror compromise and arbitrary code inside every generator.

```dockerfile
# JMeter is fetched once, at build time, and verified. Nothing is downloaded at
# run time -- an air-gapped install is a stated v1.0 goal, and the plugin rules
# in docs/architecture/07-script-repos.md exist to keep it achievable.
FROM eclipse-temurin:21-jre-jammy AS jmeter
ARG JMETER_VERSION=5.6.3
ARG JMETER_SHA512=5978a1a35edb5a7d428e270564ff49d2b1b257a65e17a759d259a9283fc17093e522fe46f474a043864aea6910683486340706d745fcdf3db1505fd71e689083
ADD https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-${JMETER_VERSION}.tgz /tmp/jmeter.tgz
RUN echo "${JMETER_SHA512}  /tmp/jmeter.tgz" | sha512sum -c - \
    && mkdir -p /opt/jmeter \
    && tar -xzf /tmp/jmeter.tgz -C /opt/jmeter --strip-components=1 \
    && rm /tmp/jmeter.tgz \
    # The GUI is never started, and shipping it enlarges the image and the
    # attack surface for no benefit.
    && rm -rf /opt/jmeter/docs /opt/jmeter/printable_docs

FROM eclipse-temurin:21-jre-jammy AS base
ENV PYTHONUNBUFFERED=1
COPY --from=jmeter /opt/jmeter /opt/jmeter

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3.12 python3.12-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /usr/local/bin/uv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
COPY apps/agent/pyproject.toml apps/agent/
COPY packages/contracts/python/pyproject.toml packages/contracts/python/
COPY packages/executor-sdk/pyproject.toml packages/executor-sdk/
RUN uv sync --locked --no-dev --no-install-workspace --package plimsoll-agent

COPY packages/contracts/python packages/contracts/python
COPY packages/executor-sdk packages/executor-sdk
COPY apps/agent apps/agent
RUN uv sync --locked --no-dev --package plimsoll-agent

RUN useradd --uid 10001 --create-home plimsoll && chown -R 10001:10001 /srv
ENV HOME=/home/plimsoll UV_NO_SYNC=1 UV_FROZEN=1 \
    PLIMSOLL_JMETER_BINARY=/opt/jmeter/bin/jmeter
USER 10001
CMD ["uv", "run", "python", "-m", "plimsoll_agent"]
```

That digest is the one Apache publishes beside the archive, and it was taken
from there rather than from a build that happened to succeed. Re-derive it with:

```bash
curl -fsSL https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz.sha512
```

If the build fails this check, stop and find out why. Relaxing it turns the one
control standing between a mirror compromise and arbitrary code inside every
generator into a comment.

- [ ] **Step 4: Build and run the tests**

Run: `make dev-down && make dev && uv run pytest apps/worker/tests/integration/test_docker_runtime.py -v -m integration`
Expected: PASS — six tests. The build is slow the first time; that is the cost accepted in the design so that a run works immediately rather than stalling mid-demo.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "feat(generator): JMeter 5.6.3, pinned and verified in the image"
```

---

### Task 5: The agent fetches, checks, and runs

**Files:**
- Create: `apps/agent/plimsoll_agent/bundle.py`, `apps/agent/plimsoll_agent/targets.py`, `apps/agent/plimsoll_agent/execution.py`, `apps/agent/tests/unit/test_targets.py`
- Modify: `apps/agent/plimsoll_agent/__main__.py`, `packages/contracts/python/plimsoll_contracts/agent.py`, `apps/api/plimsoll_api/routers/agent.py`
- Test: `apps/agent/tests/unit/test_targets.py`

**Interfaces:**
- Produces: `download(url, sha256, into) -> Path`; `refuse_disallowed(hosts, allowlist, variables) -> list[str]`; `execute(context) -> Outcome`; `Registered` gains `allowlist`, `variables`, `targets`, and `bundleSha256`.

- [ ] **Step 1: Write the failing test**

The target check is pure, and it is the one that must not be got wrong.

`apps/agent/tests/unit/test_targets.py`:

```python
"""Invariant 8's second gate, inside the generator.

The first check happened minutes earlier against configuration that could have
changed. This one happens where the traffic actually leaves the machine.
"""

import pytest

from plimsoll_agent.targets import TargetRefused, refuse_disallowed


def test_a_permitted_host_passes() -> None:
    refuse_disallowed(["demo-target"], allowlist=["demo-target"], variables={})


def test_a_host_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(TargetRefused) as raised:
        refuse_disallowed(["evil.example.com"], allowlist=["demo-target"], variables={})
    assert "evil.example.com" in str(raised.value)


def test_a_variable_host_is_resolved_before_checking() -> None:
    refuse_disallowed(
        ["${API_HOST}"], allowlist=["demo-target"], variables={"API_HOST": "demo-target"}
    )


def test_a_variable_resolving_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(TargetRefused):
        refuse_disallowed(
            ["${API_HOST}"],
            allowlist=["demo-target"],
            variables={"API_HOST": "elsewhere.invalid"},
        )


def test_an_unresolvable_variable_is_refused() -> None:
    """Unknown is not the same as permitted."""
    with pytest.raises(TargetRefused):
        refuse_disallowed(["${MISSING}"], allowlist=["demo-target"], variables={})


def test_an_empty_allowlist_permits_nothing() -> None:
    """There is no permit-all state (ADR-0007)."""
    with pytest.raises(TargetRefused):
        refuse_disallowed(["demo-target"], allowlist=[], variables={})


def test_a_suffix_rule_matches_its_subdomains() -> None:
    refuse_disallowed(["api.acme.test"], allowlist=[".acme.test"], variables={})


def test_a_suffix_rule_does_not_match_a_lookalike() -> None:
    with pytest.raises(TargetRefused):
        refuse_disallowed(["api.acme.test.evil.com"], allowlist=[".acme.test"], variables={})
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/agent/tests/unit/test_targets.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_agent.targets`.

- [ ] **Step 3: Write the agent's three new pieces**

`apps/agent/plimsoll_agent/targets.py`. The matching rules are the control
plane's, restated here because the agent cannot import `plimsoll_api` — keep the
two implementations behaviourally identical and let these tests say so:

```python
from __future__ import annotations

import ipaddress
import re

WHOLE_VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class TargetRefused(Exception):
    """A target this generator is not permitted to reach."""


def _matches(host: str, entries: list[str]) -> bool:
    candidate = host.strip().lower()
    if not candidate:
        return False
    for entry in entries:
        rule = entry.strip().lower()
        if "/" in rule:
            try:
                network = ipaddress.ip_network(rule, strict=False)
                if ipaddress.ip_address(candidate) in network:
                    return True
            except ValueError:
                continue
        elif rule.startswith("."):
            suffix = rule.removeprefix(".")
            if candidate == suffix or candidate.endswith(f".{suffix}"):
                return True
        elif candidate == rule:
            return True
    return False


def refuse_disallowed(
    hosts: list[str], *, allowlist: list[str], variables: dict[str, str]
) -> None:
    """Raises rather than reporting: this is the last gate before traffic."""
    rejected: list[str] = []
    for host in hosts:
        match = WHOLE_VARIABLE.match(host.strip())
        resolved = variables.get(match.group(1)) if match else host
        if resolved is None or "${" in resolved:
            rejected.append(f"{host} (unresolved)")
        elif not _matches(resolved, allowlist):
            rejected.append(resolved if resolved == host else f"{resolved} (from {host})")
    if rejected:
        raise TargetRefused(
            "These targets are not permitted by the organisation's policy: "
            + ", ".join(rejected)
        )
```

`apps/agent/plimsoll_agent/bundle.py`:

```python
from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

import httpx


class BundleError(Exception):
    """The bundle did not arrive, or did not arrive intact."""


def download(url: str, *, sha256: str, into: Path) -> Path:
    """The digest is checked before anything is unpacked.

    A bundle that does not match is not a plan this run was authorised to
    execute, whatever it contains.
    """
    into.mkdir(parents=True, exist_ok=True)
    archive = into / "bundle.tar.gz"
    try:
        with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
            response.raise_for_status()
            with archive.open("wb") as sink:
                for chunk in response.iter_bytes():
                    sink.write(chunk)
    except httpx.HTTPError as exc:
        raise BundleError(f"The plan bundle could not be downloaded: {exc}") from exc

    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != sha256:
        raise BundleError(f"The plan bundle digest is {actual}, expected {sha256}.")

    root = into / "plan"
    root.mkdir(exist_ok=True)
    with tarfile.open(archive) as tar:
        # filter="data" refuses absolute paths, traversal, and device nodes.
        tar.extractall(root, filter="data")
    archive.unlink()
    return root
```

`apps/agent/plimsoll_agent/execution.py`:

```python
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from plimsoll_executor.base import ExecutionContext, Outcome
from plimsoll_executor.jmeter import JMeterExecutor


async def execute(context: ExecutionContext, stop: asyncio.Event) -> Outcome:
    """Run the engine, and wind it down on request.

    A stop is a graceful shutdown: JMeter is asked to finish, and its results
    are kept. Killing it would leave a truncated JTL and lose the run.
    """
    executor = JMeterExecutor()
    context.output_directory.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *executor.command(context),
        cwd=str(context.working_directory),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        # The resolved variables reach the engine on argv inside this
        # container, and are never written to disk.
        env={**os.environ, "JVM_ARGS": os.environ.get("JVM_ARGS", "-Xms512m -Xmx1g")},
    )

    async def wind_down() -> None:
        await stop.wait()
        process.terminate()

    watcher = asyncio.create_task(wind_down())
    try:
        await process.wait()
    finally:
        watcher.cancel()
    return executor.interpret(process.returncode or 0)
```

- [ ] **Step 4: Carry what the agent needs on the wire**

In `packages/contracts/python/plimsoll_contracts/agent.py`, extend `Registered`:

```python
    plan_path: str = Field(serialization_alias="planPath")
    bundle_url: str = Field(default="", serialization_alias="bundleUrl")
    bundle_sha256: str = Field(default="", serialization_alias="bundleSha256")
    allowlist: list[str] = Field(default_factory=list)
    # Names to values, for the variables the plan references. In memory only.
    variables: dict[str, str] = Field(default_factory=dict)
```

There is deliberately no `targets` field. The agent reads the hosts out of the
plan it is about to execute rather than trusting a list something else recorded
earlier — checking a remembered list against a plan held in the hand is how the
two drift apart unnoticed.

and add the frames the artifact flow needs:

```python
class ArtifactUrlRequest(BaseModel):
    type: Literal["artifact_url_request"] = "artifact_url_request"
    name: str


class ArtifactUrl(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["artifact_url"] = "artifact_url"
    name: str
    url: str
```

In `apps/api/plimsoll_api/routers/agent.py`, fill those fields when registering.
The allowlist and the plan path come from the snapshot; the variables come from
`credentials.variables(session)`; and the bundle URL is **signed per
registration** rather than stored, because a presigned URL expires and
configuration does not:

```python
                async with session_for_org(claims.organization_id) as session:
                    variables = await credentials.variables(session)
                plan = run.configuration_snapshot["plans"][0]
                workload = run.configuration_snapshot["workload"]
                await websocket.send_text(
                    Registered(
                        desired_state=desired,
                        command=command,
                        assigned_users=mine.assigned_users,
                        duration_seconds=workload["durationSeconds"],
                        ramp_up_seconds=workload["rampUpSeconds"],
                        plan_path=plan["planPath"],
                        bundle_url=storage.presign_get(
                            storage.bundle_key(claims.run_id),
                            seconds=int(workload["durationSeconds"]) + 600,
                        ),
                        bundle_sha256=run.configuration_snapshot["bundleSha256"],
                        allowlist=run.configuration_snapshot.get("allowlist", []),
                        variables=variables,
                    ).model_dump_json(by_alias=True)
                )
```

The worker records only `bundleSha256` on the snapshot when it stages — the
digest is what the run is pinned to, and it belongs beside the commit SHAs for
the same reason. Add it in `_stage`:

```python
        async with session_for_org(org_id) as session:
            await repo.record_bundle_digest(session, run.id, bundle.sha256)
```

with the repository method:

```python
async def record_bundle_digest(
    session: AsyncSession, run_id: uuid.UUID, digest: str
) -> None:
    """Part of what the run is pinned to: the exact bytes every generator ran."""
    await session.execute(
        sa.text(
            "UPDATE test_runs "
            "SET configuration_snapshot = jsonb_set("
            "  configuration_snapshot, '{bundleSha256}', to_jsonb(:digest::text)) "
            "WHERE id = :id"
        ),
        {"id": run_id, "digest": digest},
    )
```

Import `storage` and `credentials` in the agent router.

- [ ] **Step 5: Rewrite the agent's main loop**

Replace the S3a placeholder in `apps/agent/plimsoll_agent/__main__.py`:

```python
        await channel.report(AgentState.FETCHING)
        root = download(
            registered["bundleUrl"], sha256=registered["bundleSha256"], into=Path("/tmp/run")
        )
        plan_file = root / registered["planPath"]
        variables = dict(registered["variables"])
        refuse_disallowed(
            hosts_in(plan_file.read_text(errors="replace")),
            allowlist=list(registered["allowlist"]),
            variables=variables,
        )
        await channel.report(AgentState.READY)
```

with `hosts_in` in `targets.py`:

```python
DOMAIN = re.compile(
    r'<stringProp name="HTTPSampler\.domain">([^<]*)</stringProp>'
)


def hosts_in(plan_xml: str) -> list[str]:
    """Read from the plan this generator is about to execute -- not from what
    something else recorded about it earlier."""
    return sorted({match.strip() for match in DOMAIN.findall(plan_xml) if match.strip()})
```

and on `Action.RUN`:

```python
                    outcome = await execute(
                        ExecutionContext(
                            plan_path=plan_file,
                            working_directory=plan_file.parent,
                            output_directory=Path("/tmp/out"),
                            threads=int(registered["assignedUsers"]),
                            ramp_up_seconds=int(registered["rampUpSeconds"]),
                            duration_seconds=int(registered["durationSeconds"]),
                            variables=variables,
                        ),
                        stop,
                    )
                    await upload_artifacts(channel, Path("/tmp/out"))
                    state = (
                        AgentState.COMPLETED
                        if outcome is Outcome.COMPLETED
                        else AgentState.FAILED
                    )
                    await channel.report(state)
                    return 0 if outcome is Outcome.COMPLETED else 1
```

A `TargetRefused` or `BundleError` reports `FAILED` with the exception's message
as the reason and exits non-zero, before any traffic is generated.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `uv run pytest apps/agent/tests/unit -v`
Expected: PASS — the eight target tests plus S3a's seven lifecycle tests.

- [ ] **Step 7: Commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(agent): fetch the bundle, refuse a bad target, run JMeter"
```

---

### Task 6: Artifacts, up and down

**Files:**
- Create: `apps/api/tests/integration/test_artifacts_api.py`
- Modify: `apps/agent/plimsoll_agent/__main__.py`, `apps/api/plimsoll_api/routers/agent.py`, `apps/api/plimsoll_api/routers/runs.py`
- Test: `apps/api/tests/integration/test_artifacts_api.py`

**Interfaces:**
- Produces: `upload_artifacts(channel, directory)`; `GET /runs/{id}/artifacts`; `GET /runs/{id}/artifacts/{name}` answering `302`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_artifacts_api.py`:

```python
"""What a finished run leaves behind, and how it comes back."""

import uuid

import httpx
import pytest

from plimsoll_api import storage

pytestmark = pytest.mark.integration


def test_artifacts_are_listed_for_a_run(admin_client: httpx.Client) -> None:
    run_id = uuid.uuid4()
    storage.ensure_bucket()
    storage.put_bytes(storage.artifact_key(run_id, 0, "results.jtl"), b"timeStamp,elapsed\n")

    # A run row is needed for authorisation; use one the demo project owns.
    listed = admin_client.get(f"/api/v1/runs/{run_id}/artifacts")
    assert listed.status_code == 404, "an unknown run has no artifacts to offer"


def test_a_finished_run_offers_its_jtl(admin_client: httpx.Client, completed_run: str) -> None:
    listed = admin_client.get(f"/api/v1/runs/{completed_run}/artifacts").json()
    names = {item["name"] for item in listed["items"]}
    assert "results.jtl" in names
    assert all(item["size"] > 0 for item in listed["items"])


def test_an_artifact_downloads_through_a_redirect(
    admin_client: httpx.Client, completed_run: str
) -> None:
    response = admin_client.get(
        f"/api/v1/runs/{completed_run}/artifacts/results.jtl", follow_redirects=False
    )
    assert response.status_code == 302
    body = httpx.get(_reachable(response.headers["location"])).text
    assert "timeStamp" in body.splitlines()[0]


def test_an_unknown_artifact_is_not_found(
    admin_client: httpx.Client, completed_run: str
) -> None:
    assert (
        admin_client.get(f"/api/v1/runs/{completed_run}/artifacts/nothing.txt").status_code
        == 404
    )


def test_a_traversing_name_is_refused(admin_client: httpx.Client, completed_run: str) -> None:
    response = admin_client.get(f"/api/v1/runs/{completed_run}/artifacts/..%2F..%2Fbundle.tar.gz")
    assert response.status_code in (404, 422)


def test_a_viewer_may_download(viewer_client: httpx.Client, completed_run: str) -> None:
    """Reading results is a read. TEST_READ is enough."""
    assert viewer_client.get(f"/api/v1/runs/{completed_run}/artifacts").status_code == 200
```

Add the fixture to `apps/api/tests/integration/conftest.py`, alongside `_reachable`:

```python
@pytest.fixture(scope="session")
def completed_run() -> str:
    """One real run, executed once and reused: it takes about a minute."""
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post("/api/v1/auth/login", json=ADMIN).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"
        from tests.integration.test_run_execution import (
            TERMINAL,
            _await_status,
            _short_test,
        )

        test_id = _short_test(client, seconds=15, users=2)
        run_id = client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
        _await_status(client, run_id, TERMINAL, timeout=300)
        return str(run_id)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_artifacts_api.py -v -m integration`
Expected: FAIL — `404` from a path that does not exist.

- [ ] **Step 3: Sign uploads over the channel**

In `apps/api/plimsoll_api/routers/agent.py`, answer `artifact_url_request`:

```python
            elif kind == "artifact_url_request":
                request = ArtifactUrlRequest.model_validate(raw)
                try:
                    key = storage.artifact_key(claims.run_id, claims.ordinal, request.name)
                except ValueError:
                    # The agent names the artifact; the control plane decides
                    # where it lands, and refuses a name that tries to choose.
                    await websocket.send_text('{"type":"accepted"}')
                    continue
                await websocket.send_text(
                    ArtifactUrl(
                        name=request.name, url=storage.presign_put(key)
                    ).model_dump_json(by_alias=True)
                )
```

The key is built from the token's own `run_id` and `ordinal`, never from
anything the agent sends, so an agent cannot write into another run or another
generator's prefix.

- [ ] **Step 4: Upload from the agent**

In `apps/agent/plimsoll_agent/__main__.py`:

```python
UPLOAD_ATTEMPTS = 3


async def upload_artifacts(channel: Channel, directory: Path) -> list[str]:
    """A failed upload is a warning, not a lost run.

    The run happened; refusing to record it because a log did not transfer
    would destroy more than it protects.
    """
    uploaded = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        await channel.send_raw({"type": "artifact_url_request", "name": path.name})
        reply = await channel.receive()
        if reply.get("type") != "artifact_url":
            continue
        for attempt in range(UPLOAD_ATTEMPTS):
            try:
                response = httpx.put(reply["url"], content=path.read_bytes(), timeout=300)
                response.raise_for_status()
                uploaded.append(path.name)
                break
            except httpx.HTTPError:
                if attempt == UPLOAD_ATTEMPTS - 1:
                    await channel.report(
                        AgentState.RUNNING, f"artifact {path.name} could not be uploaded"
                    )
                else:
                    await asyncio.sleep(2**attempt)
    return uploaded
```

with `Channel.send_raw` sending a plain dictionary:

```python
    async def send_raw(self, payload: dict[str, Any]) -> None:
        await self._socket.send(json.dumps(payload))
```

- [ ] **Step 5: Serve them from the API**

In `apps/api/plimsoll_api/routers/runs.py`:

```python
@router.get(
    "/api/v1/runs/{run_id}/artifacts",
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def list_artifacts(run_id: uuid.UUID, session: TenantSession) -> dict[str, Any]:
    # require() first: the object store knows nothing about tenancy, so the
    # authorisation has to happen against the run row.
    await service.require(session, run_id)
    return {"items": storage.list_prefix(f"runs/{run_id}/generators/")}


@router.get(
    "/api/v1/runs/{run_id}/artifacts/{name}",
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def download_artifact(
    run_id: uuid.UUID, name: str, session: TenantSession
) -> RedirectResponse:
    await service.require(session, run_id)
    matches = [
        item
        for item in storage.list_prefix(f"runs/{run_id}/generators/")
        if item["name"] == name
    ]
    if not matches:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such artifact.")
    return RedirectResponse(storage.presign_get(matches[0]["key"]), status_code=302)
```

importing `RedirectResponse` from `fastapi.responses` and `storage` from `plimsoll_api`. Listing rather than constructing the key is deliberate: the response can only ever name an object that exists under this run's prefix.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make dev-down && make dev && uv run pytest apps/api/tests/integration/test_artifacts_api.py -v -m integration`
Expected: PASS — six tests.

- [ ] **Step 7: Commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): artifacts uploaded by presigned URL and served back"
```

---

### Task 7: The slice demonstration and the documents

**Files:**
- Create: `apps/api/tests/integration/test_slice3_demonstration.py`
- Modify: `docs/architecture/02-execution-plane.md`, `docs/architecture/06-api.md`, `README.md`, `CLAUDE.md`
- Test: `apps/api/tests/integration/test_slice3_demonstration.py`

- [ ] **Step 1: Write the demonstration test**

`apps/api/tests/integration/test_slice3_demonstration.py`:

```python
"""The S3 promise, end to end: a defined test generates real load and leaves
artifacts an operator can download."""

import csv
import io

import httpx
import pytest

from tests.integration.conftest import _reachable
from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration


def test_a_test_runs_and_leaves_a_jtl_full_of_samples(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=20, users=4)

    created = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()
    assert len(created["configurationSnapshot"]["plans"][0]["commitSha"]) == 40

    final = _await_status(admin_client, created["id"], TERMINAL, timeout=300)
    assert final["status"] == "COMPLETED", final
    assert final["degraded"] is False

    listed = admin_client.get(f"/api/v1/runs/{created['id']}/artifacts").json()
    assert {"results.jtl", "jmeter.log"} <= {item["name"] for item in listed["items"]}

    location = admin_client.get(
        f"/api/v1/runs/{created['id']}/artifacts/results.jtl", follow_redirects=False
    ).headers["location"]
    jtl = httpx.get(_reachable(location)).text

    rows = list(csv.DictReader(io.StringIO(jtl)))
    assert rows, "the JTL has no samples: JMeter produced nothing"
    assert {"timeStamp", "elapsed", "label", "success"} <= set(rows[0])
    # The load reached the demo target, not something else.
    assert any(row["success"] == "true" for row in rows)


def test_the_generators_are_gone_afterwards(admin_client: httpx.Client) -> None:
    import subprocess

    test_id = _short_test(admin_client, seconds=15, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL, timeout=300)

    remaining = subprocess.run(  # noqa: S603
        ["docker", "ps", "-aq", "--filter", f"label=plimsoll.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert remaining == [], f"generators outlived their run: {remaining}"
```

- [ ] **Step 2: Run it**

Run: `make dev-down && make dev && uv run pytest apps/api/tests/integration/test_slice3_demonstration.py -v -m integration`
Expected: PASS. If the JTL is empty, the plan ran but reached nothing: check the agent's log with `docker compose logs worker` and the target policy allowlist, which must contain `demo-target`.

- [ ] **Step 3: Correct the documents**

In `docs/architecture/02-execution-plane.md`, replace agent step 2 ("Fetch the plan — shallow, sparse `git clone`") with the bundle, and say why in one sentence: a clone per generator would put the repository credential inside every container running a user-supplied plan. Keep the rest of the sequence.

In `docs/architecture/06-api.md`, add `GET /runs/{id}/artifacts` and `GET /runs/{id}/artifacts/{name}`, noting the `302` to a short-lived presigned URL.

In `README.md`, extend the quickstart with starting a run, polling status, and downloading the JTL — the commands verified in Step 2, not from memory.

In `CLAUDE.md`, update the status line: execution is no longer specification only.

- [ ] **Step 4: Run everything**

```bash
make dev-down && make dev
make lint && make typecheck && make test && make test-int
make contracts && git diff --quiet
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "docs: the S3 journey, from a defined test to a downloaded JTL"
```

---

## Slice acceptance

- [ ] A run generates real load: the JTL contains samples against `demo-target`
- [ ] Every generator ran byte-identical inputs, proven by the bundle digest
- [ ] No Git credential and no object-store credential exists inside a generator
- [ ] The generator image has no `git` binary and a pinned, checksum-verified JMeter
- [ ] An agent whose target fails the policy check refuses to generate traffic
- [ ] Artifacts for every generator land in object storage and download through the API
- [ ] Generators are gone after the run
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`, and `make contracts` all pass, the last leaving the tree clean

## Self-review notes

- **The agent reads target hosts from the plan, not from the wire.** Sending a stored list would have the agent check something another component recorded earlier against the plan it is actually about to execute; the two can drift, and the drift favours the attacker. `bundleSha256` on the snapshot is what ties the plan it holds to the run it was authorised for.
- **The target-matching rules exist twice**, in `plimsoll_api.services.target_policy` and `plimsoll_agent.targets`, because the agent cannot import the API package. That duplication is the cost of the trust boundary. The tests in `test_targets.py` mirror the control plane's cases on purpose; if one implementation changes, the other must, and a divergence shows up as a run that the control plane permits and the agent refuses.
- **`test_artifacts_api.py` uses a session-scoped real run.** A run takes tens of seconds, and giving every artifact test its own would make the suite unpleasant enough to skip.
- **The JMeter checksum in Task 4 is a placeholder on purpose** and will fail the build until it is replaced with the digest Apache publishes. A wrong-but-plausible checksum silently disabling verification is worse than a build that stops.
