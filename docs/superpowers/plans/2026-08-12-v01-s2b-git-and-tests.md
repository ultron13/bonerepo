# Plimsoll v0.1 Slice 2b — Git and Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A performance engineer registers a Git repository, verifies it, pins a commit, defines a test with SLA rules, and gets one answer saying whether it will run.

**Architecture:** A Git client shelling out to a real `git` binary (ref resolution by `ls-remote`, plan retrieval by blobless sparse fetch) feeds two pure parsers — one for the `.jmx`, one for `plimsoll.yaml`. `verify` composes them into a findings report; preflight composes them with the target policy and pool capacity from S2a. Network work happens **outside** any open database transaction.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 Core, Pydantic v2, `defusedxml`, `PyYAML`, nginx + `git-http-backend` for the fixture, pytest.

Design: [`docs/superpowers/specs/2026-08-12-v01-s2-domain-and-git-design.md`](../specs/2026-08-12-v01-s2-domain-and-git-design.md).
Prerequisite: [S2a](2026-08-12-v01-s2a-domain-foundations.md) — permissions, audit, credentials, projects, pools, target policy.

## Global Constraints

- Everything in S2a's Global Constraints continues to apply — no migration, `sa.text()` with bind parameters, `TenantSession`, a permission guard and an audit row on every write, `make contracts` before any commit that changes the API surface.
- **A database transaction is never held open across a network call.** Endpoints doing Git work read what they need in one transaction, close it, do the network work, then open a second transaction to write.
- Untrusted input is parsed defensively: `.jmx` through `defusedxml`, `plimsoll.yaml` through `yaml.safe_load`. Never `xml.etree` directly, never `yaml.load`.
- Secrets never reach a subprocess command line. Tokens travel by `GIT_ASKPASS` through the environment; SSH keys by a `0600` file named in `GIT_SSH_COMMAND`.
- Every Git subprocess runs under a timeout and writes into a temporary directory removed in a `finally`.

## File Structure

```
images/script-fixture/
  Dockerfile nginx.conf entrypoint.sh
  repo/perf/checkout.jmx repo/perf/plimsoll.yaml repo/perf/data/users.csv
apps/api/plimsoll_api/
  git/__init__.py git/client.py       GitAccess, resolve_ref, fetch_plan, GitError
  plans/__init__.py plans/jmx.py      PlanSummary, parse_plan
  plans/manifest.py                   Manifest, parse_manifest, ManifestError
  services/script_repos.py            CRUD rules and access assembly
  services/verify.py                  Findings from the client and the parsers
  services/script_versions.py         Pin a ref, idempotently
  services/performance_tests.py       The test document, replaced atomically
  services/preflight.py               The six checks
  repositories/script_repos.py repositories/script_versions.py
  repositories/performance_tests.py
  routers/script_repos.py routers/performance_tests.py
packages/contracts/python/plimsoll_contracts/
  scripts.py                          Repo DTOs, VerifyReport, Finding, PlanSummary
  performance_tests.py                Test document, workload, SLA rules
  validation.py                       PreflightReport, Check
apps/api/tests/unit/
  test_jmx_parser.py test_manifest_parser.py
  fixtures/plans/*.jmx fixtures/manifests/*.yaml
apps/api/tests/integration/
  test_git_client.py test_script_repos_api.py test_verify.py
  test_script_versions.py test_performance_tests_api.py test_preflight.py
```

---

### Task 1: The script fixture

**Files:**
- Create: `infrastructure/docker/script-fixture.Dockerfile`, `images/script-fixture/nginx.conf`, `images/script-fixture/entrypoint.sh`, `images/script-fixture/repo/perf/checkout.jmx`, `images/script-fixture/repo/perf/plimsoll.yaml`, `images/script-fixture/repo/perf/data/users.csv`
- Modify: `infrastructure/docker/docker-compose.yml`, `apps/api/plimsoll_api/seed.py`, `infrastructure/docker/api.Dockerfile`
- Test: `apps/api/tests/integration/test_git_client.py` (first test only — `ls-remote` reachability)

**Interfaces:**
- Produces: `http://script-fixture/public/plans.git` (anonymous) and `http://script-fixture/private/plans.git` (Basic auth, `plimsoll` / `plimsoll-fixture-token`), each with branches `main` (valid) and `broken` (manifest declares an absent data file). Seeded credential `fixture-git-token` and script repo `Demo checkout plan`.

- [ ] **Step 1: Write the plan and manifest the fixture serves**

`images/script-fixture/repo/perf/checkout.jmx` — a real JMeter 5.6 document, small but exercising every element the parser must find:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="Checkout" enabled="true">
      <boolProp name="TestPlan.functional_mode">false</boolProp>
    </TestPlan>
    <hashTree>
      <ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">
        <stringProp name="HTTPSampler.domain">demo-target</stringProp>
        <stringProp name="HTTPSampler.port">8080</stringProp>
        <stringProp name="HTTPSampler.protocol">http</stringProp>
      </ConfigTestElement>
      <hashTree/>
      <CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="Users" enabled="true">
        <stringProp name="filename">data/users.csv</stringProp>
        <stringProp name="variableNames">username,password</stringProp>
      </CSVDataSet>
      <hashTree/>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Shoppers" enabled="true">
        <stringProp name="ThreadGroup.num_threads">${__P(threads,10)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">${__P(rampup,30)}</stringProp>
      </ThreadGroup>
      <hashTree>
        <TransactionController guiclass="TransactionControllerGui" testclass="TransactionController" testname="Checkout journey" enabled="true">
          <boolProp name="TransactionController.parent">false</boolProp>
        </TransactionController>
        <hashTree>
          <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Browse" enabled="true">
            <stringProp name="HTTPSampler.path">/browse</stringProp>
            <stringProp name="HTTPSampler.method">GET</stringProp>
          </HTTPSamplerProxy>
          <hashTree/>
          <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="Checkout" enabled="true">
            <stringProp name="HTTPSampler.domain">${API_HOST}</stringProp>
            <stringProp name="HTTPSampler.protocol">http</stringProp>
            <stringProp name="HTTPSampler.path">/checkout</stringProp>
            <stringProp name="HTTPSampler.method">POST</stringProp>
          </HTTPSamplerProxy>
          <hashTree>
            <HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="Auth" enabled="true">
              <collectionProp name="HeaderManager.headers">
                <elementProp name="" elementType="Header">
                  <stringProp name="Header.name">Authorization</stringProp>
                  <stringProp name="Header.value">Bearer ${API_TOKEN}</stringProp>
                </elementProp>
              </collectionProp>
            </HeaderManager>
            <hashTree/>
          </hashTree>
        </hashTree>
        <ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think time" enabled="true">
          <stringProp name="ConstantTimer.delay">300</stringProp>
        </ConstantTimer>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
```

`images/script-fixture/repo/perf/plimsoll.yaml`:

```yaml
version: 1
engine: jmeter

jmeter:
  version: "5.6.3"

plans:
  - path: checkout.jmx
    data:
      - path: data/users.csv
    variables:
      - API_HOST
      - API_TOKEN
```

`images/script-fixture/repo/perf/data/users.csv`:

```csv
username,password
demo,demo-password
alice,alice-password
```

- [ ] **Step 2: Write the image**

The Dockerfile lives beside the other images' Dockerfiles in
`infrastructure/docker/`, as `api.Dockerfile` and `demo-target.Dockerfile` do,
and builds from the repository root.

`infrastructure/docker/script-fixture.Dockerfile`:

```dockerfile
# A Git server for verify to talk to. verify cannot be demonstrated or tested
# without one, and depending on a public host would put the internet on the
# critical path of `make dev`.
FROM alpine:3.20

RUN apk add --no-cache git nginx fcgiwrap spawn-fcgi apache2-utils

COPY images/script-fixture/nginx.conf /etc/nginx/nginx.conf
COPY images/script-fixture/entrypoint.sh /entrypoint.sh
COPY images/script-fixture/repo /seed/repo
RUN chmod +x /entrypoint.sh

EXPOSE 80
CMD ["/entrypoint.sh"]
```

`images/script-fixture/entrypoint.sh`:

```sh
#!/bin/sh
# Builds two identical repositories -- one anonymous, one behind Basic auth --
# each with a `main` branch that verifies clean and a `broken` branch whose
# manifest declares a data file that is not there.
set -eu

WORK=/tmp/work
git config --global user.email "fixture@plimsoll.dev"
git config --global user.name "Plimsoll fixture"
git config --global init.defaultBranch main

rm -rf "$WORK" && mkdir -p "$WORK"
cp -r /seed/repo/. "$WORK"
cd "$WORK"
git init --quiet
git add -A
git commit --quiet -m "Checkout plan against the demo target"

git checkout --quiet -b broken
sed -i 's|path: data/users.csv|path: data/missing.csv|' perf/plimsoll.yaml
git commit --quiet -am "Declare a data file that is not present"
git checkout --quiet main

for VISIBILITY in public private; do
    mkdir -p "/srv/git/$VISIBILITY"
    git init --quiet --bare "/srv/git/$VISIBILITY/plans.git"
    git push --quiet "/srv/git/$VISIBILITY/plans.git" main broken
    git --git-dir="/srv/git/$VISIBILITY/plans.git" config http.receivepack false
done

htpasswd -bc /etc/nginx/htpasswd plimsoll plimsoll-fixture-token

spawn-fcgi -s /var/run/fcgiwrap.sock -M 666 /usr/bin/fcgiwrap
exec nginx -g 'daemon off;'
```

`images/script-fixture/nginx.conf`:

```nginx
worker_processes 1;
events { worker_connections 64; }

http {
    access_log /dev/stdout;
    error_log /dev/stderr;

    server {
        listen 80;

        location ~ ^/public(/.*)$ {
            fastcgi_pass unix:/var/run/fcgiwrap.sock;
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME /usr/libexec/git-core/git-http-backend;
            fastcgi_param GIT_HTTP_EXPORT_ALL "";
            fastcgi_param GIT_PROJECT_ROOT /srv/git/public;
            fastcgi_param PATH_INFO $1;
        }

        location ~ ^/private(/.*)$ {
            auth_basic "Plimsoll fixture";
            auth_basic_user_file /etc/nginx/htpasswd;

            fastcgi_pass unix:/var/run/fcgiwrap.sock;
            include fastcgi_params;
            fastcgi_param SCRIPT_FILENAME /usr/libexec/git-core/git-http-backend;
            fastcgi_param GIT_HTTP_EXPORT_ALL "";
            fastcgi_param GIT_PROJECT_ROOT /srv/git/private;
            fastcgi_param PATH_INFO $1;
            fastcgi_param REMOTE_USER $remote_user;
        }
    }
}
```

In `infrastructure/docker/docker-compose.yml`, add the service and make `api` depend on it:

```yaml
  script-fixture:
    build:
      context: ../..
      dockerfile: infrastructure/docker/script-fixture.Dockerfile
    ports: ["8081:80"]
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost/public/plans.git/info/refs?service=git-upload-pack"]
      interval: 3s
      retries: 20
```

Add `script-fixture: {condition: service_healthy}` to the `api` service's `depends_on`.

- [ ] **Step 3: Give the API a git binary**

In `infrastructure/docker/api.Dockerfile`, install `git` in the runtime layer. Follow whatever package manager the base image uses — for a Debian-based image:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
```

Verify with `docker compose -f infrastructure/docker/docker-compose.yml exec -T api git --version`.

- [ ] **Step 4: Seed the credential and the repository**

In `apps/api/plimsoll_api/seed.py`, near the other constants:

```python
# The fixture's Basic-auth password. A development fixture, not a secret.
FIXTURE_TOKEN = "plimsoll:plimsoll-fixture-token"  # noqa: S105 - development seed only
FIXTURE_REPO_URL = "http://script-fixture/private/plans.git"
```

and, after the pool insert, encrypt and store it, then attach a script repo to the demo project:

```python
        ciphertext, key_ref = get_key_provider().encrypt(FIXTURE_TOKEN.encode())
        await connection.execute(
            sa.text(
                "INSERT INTO credentials (id, organization_id, name, kind, ciphertext, key_ref) "
                "VALUES (:id, :org, 'fixture-git-token', 'GIT_TOKEN', :ciphertext, :key_ref) "
                "ON CONFLICT (organization_id, name) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "org": DEMO_ORG_ID,
                "ciphertext": ciphertext,
                "key_ref": key_ref,
            },
        )

        await connection.execute(
            sa.text(
                "INSERT INTO script_repos "
                "(id, organization_id, project_id, name, repo_url, default_ref, plan_path, "
                " credential_id) "
                "SELECT :id, :org, p.id, 'Demo checkout plan', :url, 'main', 'perf/checkout.jmx', "
                "       c.id "
                "FROM projects p, credentials c "
                "WHERE p.project_key = 'DEMO' AND c.name = 'fixture-git-token' "
                "  AND NOT EXISTS (SELECT 1 FROM script_repos WHERE name = 'Demo checkout plan')"
            ),
            {"id": uuid.uuid4(), "org": DEMO_ORG_ID, "url": FIXTURE_REPO_URL},
        )
```

with `from plimsoll_api.security.secrets import get_key_provider` at the top. Re-running the seed must stay a no-op: the `NOT EXISTS` guard is what keeps `test_seeding_twice_changes_nothing` passing.

- [ ] **Step 5: Write the reachability test**

`apps/api/tests/integration/test_git_client.py` (first test only; the rest arrives in Task 2):

```python
"""The fixture is a real Git server, reachable from the API container."""

import subprocess

import pytest

pytestmark = pytest.mark.integration

COMPOSE = ["docker", "compose", "-f", "infrastructure/docker/docker-compose.yml"]


def _in_api(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [*COMPOSE, "exec", "-T", "api", *command],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_public_fixture_repository_advertises_its_branches() -> None:
    result = _in_api("git", "ls-remote", "http://script-fixture/public/plans.git")
    assert result.returncode == 0, result.stderr
    assert "refs/heads/main" in result.stdout
    assert "refs/heads/broken" in result.stdout


def test_the_private_fixture_repository_refuses_an_anonymous_caller() -> None:
    result = _in_api(
        "env", "GIT_TERMINAL_PROMPT=0", "git", "ls-remote",
        "http://script-fixture/private/plans.git",
    )
    assert result.returncode != 0
```

- [ ] **Step 6: Build and run**

Run: `make dev-down && make dev && uv run pytest apps/api/tests/integration/test_git_client.py apps/api/tests/integration/test_seed.py -v -m integration`
Expected: PASS. If `git-http-backend` is not at `/usr/libexec/git-core/git-http-backend`, find it with
`docker compose -f infrastructure/docker/docker-compose.yml exec -T script-fixture find / -name git-http-backend`
and correct `nginx.conf`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(dev): a Git server in the stack for verify to talk to"
```

---

### Task 2: The Git client

**Files:**
- Create: `apps/api/plimsoll_api/git/__init__.py`, `apps/api/plimsoll_api/git/client.py`
- Test: `apps/api/tests/integration/test_git_client.py` (extended)

**Interfaces:**
- Produces: `GitAccess(url: str, kind: str | None, secret: bytes | None)`, `GitError`, `async resolve_ref(access, ref) -> RefResolution(sha, message, committed_at)`, `async fetch_plan(access, sha, plan_path) -> AsyncIterator[Path]` (an async context manager yielding the checkout root).

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/integration/test_git_client.py`:

```python
import pytest

from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref

PUBLIC = "http://localhost:8081/public/plans.git"
PRIVATE = "http://localhost:8081/private/plans.git"
TOKEN = b"plimsoll:plimsoll-fixture-token"


async def test_a_ref_resolves_to_a_commit() -> None:
    resolution = await resolve_ref(GitAccess(PUBLIC, None, None), "main")
    assert len(resolution.sha) == 40
    assert set(resolution.sha) <= set("0123456789abcdef")


async def test_an_unknown_ref_is_an_error() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess(PUBLIC, None, None), "no-such-branch")


async def test_an_unreachable_host_is_an_error() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess("http://nowhere.invalid/r.git", None, None), "main")


async def test_a_credential_opens_the_private_repository() -> None:
    resolution = await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", TOKEN), "main")
    assert len(resolution.sha) == 40


async def test_the_private_repository_refuses_a_wrong_credential() -> None:
    with pytest.raises(GitError):
        await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", b"plimsoll:wrong"), "main")


async def test_the_plan_file_arrives_in_the_checkout() -> None:
    access = GitAccess(PUBLIC, None, None)
    resolution = await resolve_ref(access, "main")
    async with fetch_plan(access, resolution.sha, "perf/checkout.jmx") as root:
        assert (root / "perf" / "checkout.jmx").read_text().startswith("<?xml")
        assert (root / "perf" / "plimsoll.yaml").exists()
        assert (root / "perf" / "data" / "users.csv").exists()


async def test_the_checkout_is_removed_afterwards() -> None:
    access = GitAccess(PUBLIC, None, None)
    resolution = await resolve_ref(access, "main")
    async with fetch_plan(access, resolution.sha, "perf/checkout.jmx") as root:
        kept = root
    assert not kept.exists()
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest apps/api/tests/integration/test_git_client.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.git.client`.

- [ ] **Step 3: Write the client**

`apps/api/plimsoll_api/git/client.py`:

```python
"""Talking to Git, with the secret kept off the command line.

`ps` on this container shows a git invocation with no credential in it: a token
reaches git through an askpass helper and the environment, an SSH key through a
0600 file named by GIT_SSH_COMMAND. Both live inside the per-operation
temporary directory, which is removed in a finally.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

TIMEOUT_SECONDS = 60
DEFAULT_TOKEN_USERNAME = "x-access-token"

ASKPASS = """#!/bin/sh
case "$1" in
  Username*) printf '%s' "$PLIMSOLL_GIT_USERNAME" ;;
  *) printf '%s' "$PLIMSOLL_GIT_PASSWORD" ;;
esac
"""


class GitError(Exception):
    """Git was unreachable, refused the credential, or did not know the ref."""


@dataclass(frozen=True)
class GitAccess:
    url: str
    kind: str | None = None
    secret: bytes | None = None


@dataclass(frozen=True)
class RefResolution:
    sha: str
    message: str | None = None
    committed_at: datetime | None = None


@asynccontextmanager
async def _environment(access: GitAccess) -> AsyncIterator[tuple[dict[str, str], Path]]:
    workspace = Path(tempfile.mkdtemp(prefix="plimsoll-git-"))
    try:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workspace),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        if access.kind == "GIT_TOKEN" and access.secret is not None:
            secret = access.secret.decode()
            username, _, password = secret.partition(":")
            if not password:
                username, password = DEFAULT_TOKEN_USERNAME, secret
            askpass = workspace / "askpass.sh"
            askpass.write_text(ASKPASS)
            askpass.chmod(0o700)
            environment |= {
                "GIT_ASKPASS": str(askpass),
                "PLIMSOLL_GIT_USERNAME": username,
                "PLIMSOLL_GIT_PASSWORD": password,
            }
        elif access.kind == "GIT_SSH_KEY" and access.secret is not None:
            key = workspace / "id_key"
            key.write_bytes(access.secret if access.secret.endswith(b"\n") else access.secret + b"\n")
            key.chmod(0o600)
            environment["GIT_SSH_COMMAND"] = (
                f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
                "-o StrictHostKeyChecking=accept-new"
            )
        yield environment, workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


async def _run(command: list[str], *, environment: dict[str, str], cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        cwd=str(cwd) if cwd else None,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        raise GitError(f"Git did not respond within {TIMEOUT_SECONDS} seconds.") from exc
    if process.returncode != 0:
        # stderr can echo the URL but never the credential: it is not on argv.
        raise GitError(stderr.decode(errors="replace").strip() or "Git failed.")
    return stdout.decode(errors="replace")


async def resolve_ref(access: GitAccess, ref: str) -> RefResolution:
    async with _environment(access) as (environment, _):
        output = await _run(
            ["git", "ls-remote", "--exit-code", access.url, ref], environment=environment
        )
    first = output.split("\n", 1)[0].strip()
    sha = first.split("\t", 1)[0] if first else ""
    if len(sha) != 40:
        raise GitError(f"Ref {ref!r} did not resolve to a commit.")
    return RefResolution(sha=sha)


@asynccontextmanager
async def fetch_plan(access: GitAccess, sha: str, plan_path: str) -> AsyncIterator[Path]:
    """Blobless and sparse: history and unrelated blobs never cross the wire.

    The plan's own directory is checked out, which is what brings the manifest
    and the data files beside it -- everything a plan references resolves by
    relative path from the plan file.
    """
    directory = Path(plan_path).parent.as_posix()
    async with _environment(access) as (environment, workspace):
        checkout = workspace / "checkout"
        await _run(
            [
                "git", "clone", "--quiet", "--filter=blob:none", "--no-checkout",
                "--depth", "1", "--sparse", access.url, str(checkout),
            ],
            environment=environment,
        )
        await _run(["git", "fetch", "--quiet", "--depth", "1", "origin", sha],
                   environment=environment, cwd=checkout)
        await _run(
            ["git", "sparse-checkout", "set", "--no-cone", directory or "/*"],
            environment=environment,
            cwd=checkout,
        )
        await _run(["git", "checkout", "--quiet", sha], environment=environment, cwd=checkout)
        yield checkout
```

`fetch_plan` yields inside `_environment`, so the checkout disappears with the workspace when the caller's `async with` ends — which the last test asserts.

`apps/api/plimsoll_api/git/__init__.py` is empty.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_git_client.py -v -m integration`
Expected: PASS — nine tests. `--depth 1` with an explicit SHA fetch requires the server to allow ref-in-want or the SHA to be a branch tip; if the `git fetch origin <sha>` step fails against the fixture, drop `--depth 1` from the *fetch* only and record why in a comment. Do not drop `--filter=blob:none`.

- [ ] **Step 5: Prove the secret is not on the command line**

Add one more test to the same file:

```python
async def test_the_credential_is_not_visible_in_the_process_table() -> None:
    """A secret on argv is readable by any process on the host."""
    seen: list[str] = []
    original = asyncio.create_subprocess_exec

    async def recording(*command: str, **kwargs: object) -> object:
        seen.append(" ".join(command))
        return await original(*command, **kwargs)

    asyncio.create_subprocess_exec = recording  # type: ignore[assignment]
    try:
        await resolve_ref(GitAccess(PRIVATE, "GIT_TOKEN", TOKEN), "main")
    finally:
        asyncio.create_subprocess_exec = original  # type: ignore[assignment]

    assert seen
    assert not any("plimsoll-fixture-token" in command for command in seen)
```

Run it: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/api/plimsoll_api/git apps/api/tests/integration/test_git_client.py
git commit -s -m "feat(git): ref resolution and blobless plan retrieval"
```

---

### Task 3: The JMX parser

**Files:**
- Create: `apps/api/plimsoll_api/plans/__init__.py`, `apps/api/plimsoll_api/plans/jmx.py`, `apps/api/tests/unit/fixtures/plans/checkout.jmx`, `apps/api/tests/unit/fixtures/plans/entity.jmx`
- Modify: `apps/api/pyproject.toml` (add `defusedxml>=0.7`)
- Test: `apps/api/tests/unit/test_jmx_parser.py`

**Interfaces:**
- Produces: `PlanTarget(scheme, host, port)`, `PlanSummary(thread_groups, transaction_controllers, timers, variables, data_files, targets)`, `parse_plan(text: str) -> PlanSummary`, `PlanParseError`.

- [ ] **Step 1: Write the failing test**

Copy the fixture plan: `cp images/script-fixture/repo/perf/checkout.jmx apps/api/tests/unit/fixtures/plans/checkout.jmx`.

Write `apps/api/tests/unit/fixtures/plans/entity.jmx` — a plan carrying an external entity, which a naive parser would resolve:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE jmeterTestPlan [ <!ENTITY passwd SYSTEM "file:///etc/passwd"> ]>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan testclass="TestPlan" testname="&passwd;" enabled="true"/>
    <hashTree/>
  </hashTree>
</jmeterTestPlan>
```

`apps/api/tests/unit/test_jmx_parser.py`:

```python
import pathlib

import pytest

from plimsoll_api.plans.jmx import PlanParseError, PlanTarget, parse_plan

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "plans"


def _summary():
    return parse_plan((FIXTURES / "checkout.jmx").read_text())


def test_thread_groups_are_named() -> None:
    assert _summary().thread_groups == ["Shoppers"]


def test_transaction_controllers_are_named() -> None:
    assert _summary().transaction_controllers == ["Checkout journey"]


def test_timers_are_named() -> None:
    assert _summary().timers == ["Think time"]


def test_data_files_are_found() -> None:
    assert _summary().data_files == ["data/users.csv"]


def test_variables_exclude_jmeter_functions() -> None:
    # ${__P(threads,10)} is a JMeter function, not a Plimsoll variable.
    assert _summary().variables == ["API_HOST", "API_TOKEN"]


def test_targets_come_from_samplers_and_defaults() -> None:
    targets = _summary().targets
    assert PlanTarget(scheme="http", host="demo-target", port=8080) in targets
    assert PlanTarget(scheme="http", host="${API_HOST}", port=None) in targets


def test_an_external_entity_is_not_resolved() -> None:
    with pytest.raises(PlanParseError):
        parse_plan((FIXTURES / "entity.jmx").read_text())


def test_a_malformed_document_is_an_error() -> None:
    with pytest.raises(PlanParseError):
        parse_plan("<jmeterTestPlan>")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_jmx_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.plans.jmx`.

- [ ] **Step 3: Write the parser**

Add `"defusedxml>=0.7"` to `apps/api/pyproject.toml` dependencies and `uv sync`. Then `apps/api/plimsoll_api/plans/jmx.py`:

```python
"""A .jmx is executable configuration supplied by a user: untrusted input.

defusedxml refuses external entities and the billion-laughs expansion, both of
which the stock parser resolves happily inside the API container.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree.ElementTree import Element

from defusedxml.ElementTree import ParseError, fromstring

# ${NAME}, excluding ${__function(...)} which JMeter resolves itself.
VARIABLE = re.compile(r"\$\{(?!__)([A-Za-z_][A-Za-z0-9_]*)\}")

DOMAIN_PROP = "HTTPSampler.domain"
PORT_PROP = "HTTPSampler.port"
PROTOCOL_PROP = "HTTPSampler.protocol"


class PlanParseError(Exception):
    """The document is not well-formed, or resolves entities we refuse."""


@dataclass(frozen=True)
class PlanTarget:
    scheme: str
    host: str
    port: int | None


@dataclass(frozen=True)
class PlanSummary:
    thread_groups: list[str] = field(default_factory=list)
    transaction_controllers: list[str] = field(default_factory=list)
    timers: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    data_files: list[str] = field(default_factory=list)
    targets: list[PlanTarget] = field(default_factory=list)


def _name(element: Element) -> str:
    return element.get("testname", "").strip()


def _string_props(element: Element) -> dict[str, str]:
    return {
        prop.get("name", ""): (prop.text or "").strip()
        for prop in element.findall("stringProp")
    }


def parse_plan(text: str) -> PlanSummary:
    try:
        root = fromstring(text)
    except (ParseError, ValueError) as exc:
        raise PlanParseError(str(exc)) from exc

    thread_groups: list[str] = []
    controllers: list[str] = []
    timers: list[str] = []
    data_files: list[str] = []
    targets: list[PlanTarget] = []
    defaults: dict[str, str] = {}

    for element in root.iter():
        testclass = element.get("testclass", "")
        if testclass.endswith("ThreadGroup"):
            thread_groups.append(_name(element))
        elif testclass == "TransactionController":
            controllers.append(_name(element))
        elif testclass.endswith("Timer"):
            timers.append(_name(element))
        elif testclass == "CSVDataSet":
            filename = _string_props(element).get("filename", "")
            if filename:
                data_files.append(filename)
        elif testclass == "ConfigTestElement":
            props = _string_props(element)
            if props.get(DOMAIN_PROP):
                defaults = props
        elif testclass == "HTTPSamplerProxy":
            props = _string_props(element)
            host = props.get(DOMAIN_PROP) or defaults.get(DOMAIN_PROP, "")
            if not host:
                continue
            scheme = props.get(PROTOCOL_PROP) or defaults.get(PROTOCOL_PROP) or "http"
            port = props.get(PORT_PROP) or defaults.get(PORT_PROP) or ""
            targets.append(
                PlanTarget(scheme=scheme, host=host, port=int(port) if port.isdigit() else None)
            )

    if defaults.get(DOMAIN_PROP):
        default_port = defaults.get(PORT_PROP, "")
        targets.append(
            PlanTarget(
                scheme=defaults.get(PROTOCOL_PROP) or "http",
                host=defaults[DOMAIN_PROP],
                port=int(default_port) if default_port.isdigit() else None,
            )
        )

    return PlanSummary(
        thread_groups=sorted({name for name in thread_groups if name}),
        transaction_controllers=sorted({name for name in controllers if name}),
        timers=sorted({name for name in timers if name}),
        variables=sorted(set(VARIABLE.findall(text))),
        data_files=sorted(set(data_files)),
        targets=sorted(set(targets), key=lambda target: (target.host, target.port or 0)),
    )
```

Variables are collected from the document text rather than per element, because a reference can appear in an attribute, a header value, a path, or a body — enumerating the places it may hide is how one gets missed.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_jmx_parser.py -v`
Expected: PASS — eight tests. `test_an_external_entity_is_not_resolved` passing is the one that matters: if it fails, `defusedxml` is not actually in the import path.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "feat(plans): parse a JMeter plan as untrusted input"
```

---

### Task 4: The manifest parser

**Files:**
- Create: `apps/api/plimsoll_api/plans/manifest.py`, `apps/api/tests/unit/fixtures/manifests/valid.yaml`, `apps/api/tests/unit/fixtures/manifests/secret.yaml`
- Modify: `apps/api/pyproject.toml` (add `pyyaml>=6.0`)
- Test: `apps/api/tests/unit/test_manifest_parser.py`

**Interfaces:**
- Produces: `Manifest`, `ManifestPlan`, `ManifestData`, `ManifestPlugin`, `parse_manifest(text: str) -> Manifest`, `ManifestError`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/fixtures/manifests/valid.yaml` is a copy of the fixture repository's manifest with a plugin added:

```yaml
version: 1
engine: jmeter

jmeter:
  version: "5.6.3"

plugins:
  - id: jpgc-casutg
    version: "2.10"
    sha256: "9f2c4a00000000000000000000000000000000000000000000000000000000ff"

plans:
  - path: checkout.jmx
    data:
      - path: data/users.csv
        distribution: shared
    variables:
      - API_HOST
      - API_TOKEN
```

`apps/api/tests/unit/fixtures/manifests/secret.yaml`:

```yaml
version: 1
engine: jmeter
plans:
  - path: checkout.jmx
    variables:
      - API_TOKEN: ghp_averyrealisticlookingtoken
```

`apps/api/tests/unit/test_manifest_parser.py`:

```python
import pathlib

import pytest

from plimsoll_api.plans.manifest import ManifestError, parse_manifest

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "manifests"


def test_a_valid_manifest_parses() -> None:
    manifest = parse_manifest((FIXTURES / "valid.yaml").read_text())
    assert manifest.version == 1
    assert manifest.jmeter_version == "5.6.3"
    assert manifest.plans[0].path == "checkout.jmx"
    assert manifest.plans[0].variables == ["API_HOST", "API_TOKEN"]
    assert manifest.plans[0].data[0].path == "data/users.csv"
    assert manifest.plans[0].data[0].distribution == "shared"
    assert manifest.plugins[0].id == "jpgc-casutg"


def test_a_variable_carrying_a_value_is_refused() -> None:
    """Values come from the platform's secret store, never the manifest."""
    with pytest.raises(ManifestError) as raised:
        parse_manifest((FIXTURES / "secret.yaml").read_text())
    assert "API_TOKEN" in str(raised.value)


def test_an_unsupported_schema_version_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("version: 2\nengine: jmeter\nplans: []\n")


def test_an_unpinned_plugin_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(
            "version: 1\nengine: jmeter\nplugins:\n  - id: jpgc-casutg\nplans: []\n"
        )


def test_a_plugin_without_a_checksum_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest(
            'version: 1\nengine: jmeter\nplugins:\n  - id: jpgc-casutg\n'
            '    version: "2.10"\nplans: []\n'
        )


def test_the_distribution_defaults_to_shared() -> None:
    manifest = parse_manifest(
        "version: 1\nengine: jmeter\nplans:\n  - path: p.jmx\n"
        "    data:\n      - path: data/a.csv\n"
    )
    assert manifest.plans[0].data[0].distribution == "shared"


def test_malformed_yaml_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest("version: 1\n  engine: [unclosed\n")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_manifest_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_api.plans.manifest`.

- [ ] **Step 3: Write the parser**

Add `"pyyaml>=6.0"` and `"types-pyyaml>=6.0"` to `apps/api/pyproject.toml` (the second in the dev group, for mypy) and `uv sync`. Then `apps/api/plimsoll_api/plans/manifest.py`:

```python
"""plimsoll.yaml -- the tester-owned half of the contract.

Two rules are enforced here rather than left to review: variables are names and
never values, and a plugin pins both a version and a checksum. An unpinned
plugin is a supply-chain hole and an unreproducible run.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

SUPPORTED_VERSION = 1
SHARED = "shared"


class ManifestError(Exception):
    """The manifest is malformed, unsupported, or breaks a contract rule."""


class ManifestData(BaseModel):
    path: str = Field(min_length=1)
    distribution: str = SHARED


class ManifestPlan(BaseModel):
    path: str = Field(min_length=1)
    data: list[ManifestData] = Field(default_factory=list)
    variables: list[str] = Field(default_factory=list)

    @field_validator("variables", mode="before")
    @classmethod
    def _names_only(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        for entry in value:
            if isinstance(entry, dict):
                named = ", ".join(str(key) for key in entry)
                raise ValueError(
                    f"variable {named} carries a value; manifests declare names only, "
                    "and values resolve from the platform's secret store at run start"
                )
        return value


class ManifestPlugin(BaseModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: str = Field(min_length=1)


class Manifest(BaseModel):
    version: int
    engine: str = "jmeter"
    jmeter_version: str | None = None
    plugins: list[ManifestPlugin] = Field(default_factory=list)
    plans: list[ManifestPlan] = Field(default_factory=list)


def parse_manifest(text: str) -> Manifest:
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError(f"The manifest is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise ManifestError("The manifest must be a mapping.")
    if document.get("version") != SUPPORTED_VERSION:
        raise ManifestError(
            f"Manifest schema version {document.get('version')!r} is not supported; "
            f"this Plimsoll understands version {SUPPORTED_VERSION}."
        )

    payload = dict(document)
    jmeter = payload.pop("jmeter", None)
    if isinstance(jmeter, dict):
        payload["jmeter_version"] = jmeter.get("version")

    try:
        return Manifest.model_validate(payload)
    except ValidationError as exc:
        raise ManifestError(_readable(exc)) from exc


def _readable(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    )
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit/test_manifest_parser.py -v`
Expected: PASS — seven tests.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "feat(plans): parse and enforce the plimsoll.yaml contract"
```

---

### Task 5: Script repositories

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/scripts.py`, `apps/api/plimsoll_api/repositories/script_repos.py`, `apps/api/plimsoll_api/services/script_repos.py`, `apps/api/plimsoll_api/routers/script_repos.py`
- Modify: `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_script_repos_api.py`

**Interfaces:**
- Produces: contracts `ScriptRepoCreate`, `ScriptRepoUpdate`, `ScriptRepoResponse`; `script_repos.require(session, repo_id)`; `script_repos.access_for(session, row) -> GitAccess` — the single place a credential is decrypted for Git, consumed by `verify` and by version pinning.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_script_repos_api.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

FIXTURE_URL = "http://script-fixture/public/plans.git"


def _project(client: httpx.Client) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Repo holder", "projectKey": f"R{uuid.uuid4().hex[:8].upper()}"},
    )
    return str(response.json()["id"])


def _create(client: httpx.Client, project_id: str, **overrides: object) -> dict[str, object]:
    body = {
        "name": f"repo-{uuid.uuid4().hex[:6]}",
        "repoUrl": FIXTURE_URL,
        "planPath": "perf/checkout.jmx",
        "defaultRef": "main",
    } | overrides
    response = client.post(f"/api/v1/projects/{project_id}/script-repos", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_a_repository_is_registered(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project(admin_client))
    assert created["planPath"] == "perf/checkout.jmx"
    assert created["engine"] == "jmeter"
    assert created["credentialId"] is None


def test_it_is_listed_under_its_project(admin_client: httpx.Client) -> None:
    project_id = _project(admin_client)
    created = _create(admin_client, project_id)
    listed = admin_client.get(f"/api/v1/projects/{project_id}/script-repos").json()
    assert [item["id"] for item in listed["items"]] == [created["id"]]


def test_a_repository_in_an_unknown_project_is_not_found(admin_client: httpx.Client) -> None:
    response = admin_client.post(
        f"/api/v1/projects/{uuid.uuid4()}/script-repos",
        json={"name": "orphan", "repoUrl": FIXTURE_URL, "planPath": "perf/checkout.jmx"},
    )
    assert response.status_code == 404


def test_an_unsupported_url_scheme_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project(admin_client)
    response = admin_client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={"name": "bad", "repoUrl": "file:///etc/passwd", "planPath": "p.jmx"},
    )
    assert response.status_code == 422


def test_a_repository_is_updated_and_archived(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project(admin_client))
    updated = admin_client.patch(
        f"/api/v1/script-repos/{created['id']}", json={"defaultRef": "broken"}
    )
    assert updated.status_code == 200
    assert updated.json()["defaultRef"] == "broken"
    assert admin_client.delete(f"/api/v1/script-repos/{created['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/script-repos/{created['id']}").json()["status"] == "ARCHIVED"


def test_a_viewer_cannot_register_one(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    project_id = _project(admin_client)
    response = viewer_client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={"name": "nope", "repoUrl": FIXTURE_URL, "planPath": "perf/checkout.jmx"},
    )
    assert response.status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Expected: FAIL — `404` from the project-scoped create.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/scripts.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_SCHEMES = ("http://", "https://", "ssh://", "git@")


class ScriptRepoCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    repo_url: str = Field(min_length=1, alias="repoUrl")
    plan_path: str = Field(min_length=1, alias="planPath")
    default_ref: str = Field(default="main", max_length=255, alias="defaultRef")
    credential_id: uuid.UUID | None = Field(default=None, alias="credentialId")

    @field_validator("repo_url")
    @classmethod
    def _supported_scheme(cls, value: str) -> str:
        if not value.startswith(ALLOWED_SCHEMES):
            raise ValueError(
                "a repository URL must be http, https, or ssh; "
                "local paths are not fetched by the control plane"
            )
        return value

    @field_validator("plan_path")
    @classmethod
    def _inside_the_repository(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("a plan path is relative to the repository root")
        return value


class ScriptRepoUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    plan_path: str | None = Field(default=None, min_length=1, alias="planPath")
    default_ref: str | None = Field(default=None, max_length=255, alias="defaultRef")
    credential_id: uuid.UUID | None = Field(default=None, alias="credentialId")


class ScriptRepoResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    name: str
    engine: str
    repo_url: str = Field(serialization_alias="repoUrl")
    default_ref: str = Field(serialization_alias="defaultRef")
    plan_path: str = Field(serialization_alias="planPath")
    credential_id: uuid.UUID | None = Field(serialization_alias="credentialId")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class FindingSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class Finding(BaseModel):
    code: str
    severity: FindingSeverity
    message: str
    location: str | None = None


class PlanTargetResponse(BaseModel):
    scheme: str
    host: str
    port: int | None


class PlanSummaryResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    thread_groups: list[str] = Field(serialization_alias="threadGroups")
    transaction_controllers: list[str] = Field(serialization_alias="transactionControllers")
    timers: list[str]
    variables: list[str]
    data_files: list[str] = Field(serialization_alias="dataFiles")
    targets: list[PlanTargetResponse]


class PluginSummary(BaseModel):
    id: str
    version: str
    sha256: str


class VerifyReport(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    ok: bool
    commit_sha: str | None = Field(default=None, serialization_alias="commitSha")
    ref: str
    findings: list[Finding] = Field(default_factory=list)
    plan: PlanSummaryResponse | None = None
    plugins: list[PluginSummary] = Field(default_factory=list)


class ScriptVersionResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    script_repo_id: uuid.UUID = Field(serialization_alias="scriptRepoId")
    commit_sha: str = Field(serialization_alias="commitSha")
    plan_path: str = Field(serialization_alias="planPath")
    checksum: str | None
    resolved_at: datetime = Field(serialization_alias="resolvedAt")


class ScriptVersionCreate(BaseModel):
    # Absent means the repository's default_ref.
    ref: str | None = Field(default=None, max_length=255)
```

- [ ] **Step 4: Write the repository, service, and router**

`repositories/script_repos.py` mirrors `repositories/projects.py` with
`COLUMNS = "id, project_id, name, engine, repo_url, default_ref, plan_path, credential_id, status, created_at, updated_at"`, plus `list_page_for_project(session, project_id, limit, after)` adding `WHERE project_id = :project` to the keyset clause.

`services/script_repos.py`:

```python
async def create(session, principal, project_id, body) -> Any:
    await projects_service.require(session, project_id)   # 404 outside the organisation
    if body.credential_id is not None and await credentials_repo.get(session, body.credential_id) is None:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, "No such credential.", {"credentialId": str(body.credential_id)})
    row = await repo.insert(...)
    await audit.record(session, principal=principal, action="script_repo.created",
                       entity_type="script_repo", entity_id=row.id)
    return row


async def require(session, repo_id) -> Any:
    row = await repo.get(session, repo_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such script repository.")
    return row


async def access_for(session, row) -> GitAccess:
    """The one place a credential is decrypted for Git."""
    if row.credential_id is None:
        return GitAccess(url=row.repo_url)
    material = await credentials_repo.secret_material(session, row.credential_id)
    if material is None:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, "The repository's credential no longer exists.")
    return GitAccess(
        url=row.repo_url,
        kind=material.kind,
        secret=get_key_provider().decrypt(material.ciphertext, material.key_ref),
    )
```

with `update` (`UPDATABLE = {"name", "plan_path", "default_ref", "credential_id"}`, audit `script_repo.updated`) and `archive` (audit `script_repo.deleted`) following `services/projects.py` exactly.

`routers/script_repos.py` carries both prefixes in one router file, because they are the same resource:
`POST`/`GET /api/v1/projects/{project_id}/script-repos` and `GET`/`PATCH`/`DELETE /api/v1/script-repos/{repo_id}`. Reads take `Permission.SCRIPT_READ`, writes `Permission.SCRIPT_WRITE`. Register it in `main.py`.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_script_repos_api.py -v -m integration`
Expected: PASS — six tests.

- [ ] **Step 6: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): script repositories under their project"
```

---

### Task 6: verify

**Files:**
- Create: `apps/api/plimsoll_api/services/verify.py`
- Modify: `apps/api/plimsoll_api/routers/script_repos.py`
- Test: `apps/api/tests/integration/test_verify.py`

**Interfaces:**
- Consumes: `git.client`, `plans.jmx`, `plans.manifest`, `script_repos.access_for`.
- Produces: `verify.run(access, plan_path, ref) -> VerifyReport`; `POST /api/v1/script-repos/{id}/verify`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_verify.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

PUBLIC = "http://script-fixture/public/plans.git"
PRIVATE = "http://script-fixture/private/plans.git"


def _repo(client: httpx.Client, **overrides: object) -> dict[str, object]:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Verify", "projectKey": f"V{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    body = {
        "name": f"repo-{uuid.uuid4().hex[:6]}",
        "repoUrl": PUBLIC,
        "planPath": "perf/checkout.jmx",
        "defaultRef": "main",
    } | overrides
    return client.post(f"/api/v1/projects/{project['id']}/script-repos", json=body).json()


def _credential_id(client: httpx.Client) -> str:
    items = client.get("/api/v1/credentials").json()["items"]
    return next(str(item["id"]) for item in items if item["name"] == "fixture-git-token")


def test_a_conformant_repository_verifies_clean(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    response = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body["findings"]
    assert len(body["commitSha"]) == 40
    assert body["plan"]["threadGroups"] == ["Shoppers"]
    assert body["plan"]["dataFiles"] == ["data/users.csv"]
    assert {"API_HOST", "API_TOKEN"} <= set(body["plan"]["variables"])
    # The plan's ${API_HOST} target cannot be checked without a test context,
    # so verify says so as a warning rather than passing it over in silence.
    unresolved = [f for f in body["findings"] if f["code"] == "TARGET_UNRESOLVED"]
    assert unresolved and unresolved[0]["severity"] == "WARNING"


def test_the_seeded_repository_verifies_through_its_credential(admin_client: httpx.Client) -> None:
    repos = admin_client.get("/api/v1/credentials").json()
    assert repos["items"], "the seed must provide the fixture credential"
    repo = _repo(admin_client, repoUrl=PRIVATE, credentialId=_credential_id(admin_client))
    body = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify").json()
    assert body["ok"] is True, body["findings"]


def test_a_missing_credential_reaches_repo_unreachable(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client, repoUrl=PRIVATE)
    response = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"


def test_an_unknown_ref_reaches_repo_unreachable(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client, defaultRef="no-such-branch")
    response = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"


def test_a_declared_but_absent_data_file_is_a_finding(admin_client: httpx.Client) -> None:
    """Reachable but non-conformant is 200 with findings, not an error."""
    repo = _repo(admin_client, defaultRef="broken")
    response = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    codes = {finding["code"] for finding in body["findings"]}
    assert "DATA_FILE_MISSING" in codes
    assert body["plan"] is not None, "the plan still parsed; report what was learned"


def test_a_missing_plan_is_a_finding(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client, planPath="perf/nowhere.jmx")
    body = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify").json()
    assert body["ok"] is False
    assert {"PLAN_MISSING"} == {finding["code"] for finding in body["findings"]}


def test_verifying_is_audited(admin_client: httpx.Client) -> None:
    import anyio
    import sqlalchemy as sa

    from plimsoll_api.db.session import session_for_org

    repo = _repo(admin_client)
    admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify")
    org = uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])

    async def count() -> int:
        async with session_for_org(org) as session:
            value: int = await session.scalar(
                sa.text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE action = 'script_repo.verified' AND entity_id = :id"
                ),
                {"id": uuid.UUID(str(repo["id"]))},
            )
        return value

    assert anyio.run(count) == 1


def test_a_viewer_cannot_verify(admin_client: httpx.Client, viewer_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    assert viewer_client.post(f"/api/v1/script-repos/{repo['id']}/verify").status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Expected: FAIL — `404` from the verify path.

- [ ] **Step 3: Write the service**

`apps/api/plimsoll_api/services/verify.py`:

```python
"""verify reports; it does not reject.

A reachable repository that does not conform returns every finding at once, so
the tester fixes the set rather than discovering them one request at a time.
REPO_UNREACHABLE is reserved for not reaching Git at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from plimsoll_api.errors import PlimsollError
from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref
from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, parse_plan
from plimsoll_api.plans.manifest import Manifest, ManifestError, parse_manifest
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.scripts import (
    Finding,
    FindingSeverity,
    PlanSummaryResponse,
    PlanTargetResponse,
    PluginSummary,
    VerifyReport,
)

MANIFEST_NAME = "plimsoll.yaml"


def _error(code: str, message: str, location: str | None = None) -> Finding:
    return Finding(
        code=code, severity=FindingSeverity.ERROR, message=message, location=location
    )


def _warning(code: str, message: str, location: str | None = None) -> Finding:
    return Finding(
        code=code, severity=FindingSeverity.WARNING, message=message, location=location
    )


def _unresolved_targets(summary: PlanSummary, plan_path: str) -> list[Finding]:
    """A host like ${API_HOST} cannot be checked here.

    verify has no test context and therefore no variable values, so it says so
    rather than guessing. Preflight resolves the variable and checks the real
    host against the allowlist.
    """
    return [
        _warning(
            "TARGET_UNRESOLVED",
            f"Target host {target.host} is a variable; it is checked against the "
            "target policy at preflight, when its value is known.",
            plan_path,
        )
        for target in summary.targets
        if "${" in target.host
    ]


def _find_manifest(root: Path, plan_path: str) -> Path | None:
    """The plan's directory first, then upward to the repository root."""
    directory = (root / plan_path).parent
    while True:
        candidate = directory / MANIFEST_NAME
        if candidate.is_file():
            return candidate
        if directory == root:
            return None
        directory = directory.parent


def _summary_response(summary: PlanSummary) -> PlanSummaryResponse:
    return PlanSummaryResponse(
        thread_groups=summary.thread_groups,
        transaction_controllers=summary.transaction_controllers,
        timers=summary.timers,
        variables=summary.variables,
        data_files=summary.data_files,
        targets=[
            PlanTargetResponse(scheme=target.scheme, host=target.host, port=target.port)
            for target in summary.targets
        ],
    )


def _manifest_findings(
    manifest: Manifest, plan_path: str, summary: PlanSummary, root: Path
) -> list[Finding]:
    findings: list[Finding] = []
    plan_name = Path(plan_path).name
    entry = next(
        (plan for plan in manifest.plans if Path(plan.path).name == plan_name), None
    )
    if entry is None:
        findings.append(
            _error(
                "PLAN_NOT_DECLARED",
                f"{plan_path} is not listed under `plans` in the manifest.",
                MANIFEST_NAME,
            )
        )
        return findings

    plan_directory = (root / plan_path).parent
    for data in entry.data:
        if not (plan_directory / data.path).is_file():
            findings.append(
                _error(
                    "DATA_FILE_MISSING",
                    f"{data.path} is declared in the manifest but absent at this commit.",
                    MANIFEST_NAME,
                )
            )

    declared = {data.path for data in entry.data}
    for referenced in summary.data_files:
        if referenced not in declared and not (plan_directory / referenced).is_file():
            findings.append(
                _error(
                    "DATA_FILE_UNDECLARED",
                    f"The plan reads {referenced}, which is neither declared nor present.",
                    plan_path,
                )
            )

    undeclared = sorted(set(summary.variables) - set(entry.variables))
    if undeclared:
        findings.append(
            _error(
                "VARIABLE_NOT_DECLARED",
                "The plan references variables the manifest does not declare: "
                + ", ".join(undeclared),
                MANIFEST_NAME,
            )
        )
    return findings


async def run(access: GitAccess, plan_path: str, ref: str) -> VerifyReport:
    try:
        resolution = await resolve_ref(access, ref)
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE,
            f"Could not resolve {ref!r} in the repository: {exc}",
            {"ref": ref},
        ) from exc

    findings: list[Finding] = []
    summary: PlanSummary | None = None
    plugins: list[PluginSummary] = []

    try:
        async with fetch_plan(access, resolution.sha, plan_path) as root:
            plan_file = root / plan_path
            if not plan_file.is_file():
                findings.append(
                    _error("PLAN_MISSING", f"No plan at {plan_path} in commit {resolution.sha}.")
                )
            else:
                try:
                    summary = parse_plan(plan_file.read_text(errors="replace"))
                except PlanParseError as exc:
                    findings.append(_error("PLAN_UNPARSEABLE", str(exc), plan_path))
                else:
                    findings.extend(_unresolved_targets(summary, plan_path))

                manifest_file = _find_manifest(root, plan_path)
                if manifest_file is None:
                    # Optional: a repository needing nothing beyond stock JMeter
                    # is verified from the plan alone.
                    pass
                else:
                    try:
                        manifest = parse_manifest(manifest_file.read_text(errors="replace"))
                    except ManifestError as exc:
                        findings.append(_error("MANIFEST_INVALID", str(exc), MANIFEST_NAME))
                    else:
                        plugins = [
                            PluginSummary(id=p.id, version=p.version, sha256=p.sha256)
                            for p in manifest.plugins
                        ]
                        if summary is not None:
                            findings.extend(
                                _manifest_findings(manifest, plan_path, summary, root)
                            )
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not read the repository: {exc}"
        ) from exc

    return VerifyReport(
        # A warning is something to know, not something to fix before running.
        ok=not any(finding.severity is FindingSeverity.ERROR for finding in findings),
        commit_sha=resolution.sha,
        ref=ref,
        findings=findings,
        plan=_summary_response(summary) if summary is not None else None,
        plugins=plugins,
    )


def plan_checksum(plan_file: Path) -> str:
    return hashlib.sha256(plan_file.read_bytes()).hexdigest()
```

- [ ] **Step 4: Write the endpoint**

In `routers/script_repos.py`, and note the shape — **no session dependency**, because a transaction must not stay open across the clone:

```python
@router.post(
    "/api/v1/script-repos/{repo_id}/verify",
    response_model=VerifyReport,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def verify_repo(repo_id: uuid.UUID, principal: CurrentPrincipal) -> VerifyReport:
    async with session_for_org(principal.organization_id) as session:
        row = await service.require(session, repo_id)
        access = await service.access_for(session, row)
        plan_path, ref = row.plan_path, row.default_ref

    report = await verify.run(access, plan_path, ref)

    async with session_for_org(principal.organization_id) as session:
        await audit.record(
            session,
            principal=principal,
            action="script_repo.verified",
            entity_type="script_repo",
            entity_id=repo_id,
            metadata={"ok": report.ok, "commitSha": report.commit_sha},
        )
    return report
```

`verify` requires `SCRIPT_WRITE` rather than a read permission: it makes the platform reach out to a third-party host on the caller's behalf, which is a side effect a read-only principal should not be able to trigger.

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_verify.py -v -m integration`
Expected: PASS — eight tests. If `test_a_missing_credential_reaches_repo_unreachable` returns `500`, the Git failure is escaping as something other than `GitError` — fix the client's error wrapping, not the test.

- [ ] **Step 6: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): verify a script repository and report every finding at once"
```

---

### Task 7: Pinned versions

**Files:**
- Create: `apps/api/plimsoll_api/repositories/script_versions.py`, `apps/api/plimsoll_api/services/script_versions.py`
- Modify: `apps/api/plimsoll_api/routers/script_repos.py`
- Test: `apps/api/tests/integration/test_script_versions.py`

**Interfaces:**
- Produces: `script_versions.pin(session_factory, principal, repo_row, ref) -> row`; `POST`/`GET /api/v1/script-repos/{id}/versions`, `GET /api/v1/script-versions/{id}`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_script_versions.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

PUBLIC = "http://script-fixture/public/plans.git"


def _repo(client: httpx.Client) -> dict[str, object]:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Pinning", "projectKey": f"P{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    return client.post(
        f"/api/v1/projects/{project['id']}/script-repos",
        json={
            "name": f"repo-{uuid.uuid4().hex[:6]}",
            "repoUrl": PUBLIC,
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
        },
    ).json()


def test_a_ref_pins_to_a_commit(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    response = admin_client.post(f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["commitSha"]) == 40
    assert body["planPath"] == "perf/checkout.jmx"
    assert body["checksum"]


def test_pinning_the_same_commit_twice_returns_the_same_version(
    admin_client: httpx.Client,
) -> None:
    repo = _repo(admin_client)
    first = admin_client.post(f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"})
    second = admin_client.post(f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"})
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    listed = admin_client.get(f"/api/v1/script-repos/{repo['id']}/versions").json()
    assert len(listed["items"]) == 1


def test_two_branches_pin_to_two_versions(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    admin_client.post(f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"})
    admin_client.post(f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "broken"})
    assert len(admin_client.get(f"/api/v1/script-repos/{repo['id']}/versions").json()["items"]) == 2


def test_a_version_is_fetched_by_id(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    created = admin_client.post(
        f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"}
    ).json()
    fetched = admin_client.get(f"/api/v1/script-versions/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["commitSha"] == created["commitSha"]


def test_pinning_omits_the_ref_and_uses_the_default(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    assert admin_client.post(
        f"/api/v1/script-repos/{repo['id']}/versions", json={}
    ).status_code == 201


def test_an_unknown_ref_is_repo_unreachable(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    response = admin_client.post(
        f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "no-such-branch"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"
```

- [ ] **Step 2: Run it to make sure it fails**

Expected: FAIL — `404` from the versions path.

- [ ] **Step 3: Write the repository and service**

`repositories/script_versions.py` provides `insert`, `by_commit(session, repo_id, sha, plan_path)`, `get`, and `list_page_for_repo`, with `COLUMNS = "id, script_repo_id, commit_sha, plan_path, checksum, resolved_at"`, and `insert` storing `metadata` from the verify plan summary through `CAST(:metadata AS jsonb)`.

`services/script_versions.py` — the network half only, so the router owns both transactions and neither is open during the fetch:

```python
"""Pinning is idempotent by construction.

A commit SHA is immutable, so pinning the same (repo, sha, plan_path) twice
describes the same thing and returns the row that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from plimsoll_api.errors import PlimsollError
from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref
from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, parse_plan
from plimsoll_api.services.verify import plan_checksum
from plimsoll_contracts.errors import ErrorCode


@dataclass(frozen=True)
class PinCandidate:
    sha: str
    checksum: str
    summary: PlanSummary


async def resolve_for_pin(access: GitAccess, plan_path: str, ref: str) -> PinCandidate:
    try:
        resolution = await resolve_ref(access, ref)
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not resolve {ref!r}: {exc}", {"ref": ref}
        ) from exc

    try:
        async with fetch_plan(access, resolution.sha, plan_path) as root:
            plan_file = root / plan_path
            if not plan_file.is_file():
                raise PlimsollError(
                    ErrorCode.VALIDATION_FAILED,
                    f"No plan at {plan_path} in commit {resolution.sha}.",
                )
            checksum = plan_checksum(plan_file)
            try:
                summary = parse_plan(plan_file.read_text(errors="replace"))
            except PlanParseError as exc:
                raise PlimsollError(
                    ErrorCode.VALIDATION_FAILED, f"The plan does not parse: {exc}"
                ) from exc
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not read the repository: {exc}"
        ) from exc

    return PinCandidate(sha=resolution.sha, checksum=checksum, summary=summary)
```

The router in `routers/script_repos.py`:

```python
@router.post(
    "/api/v1/script-repos/{repo_id}/versions",
    response_model=ScriptVersionResponse,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def pin_version(
    repo_id: uuid.UUID,
    body: ScriptVersionCreate,
    principal: CurrentPrincipal,
    response: Response,
) -> ScriptVersionResponse:
    async with session_for_org(principal.organization_id) as session:
        row = await service.require(session, repo_id)
        access = await service.access_for(session, row)
        plan_path, ref = row.plan_path, body.ref or row.default_ref

    candidate = await script_versions.resolve_for_pin(access, plan_path, ref)

    async with session_for_org(principal.organization_id) as session:
        existing = await versions_repo.by_commit(session, repo_id, candidate.sha, plan_path)
        if existing is not None:
            # Already pinned: the same commit, the same plan, the same row.
            response.status_code = 200
            return _version_response(existing)

        created = await versions_repo.insert(
            session,
            org_id=principal.organization_id,
            script_repo_id=repo_id,
            commit_sha=candidate.sha,
            plan_path=plan_path,
            checksum=candidate.checksum,
            metadata={
                "threadGroups": candidate.summary.thread_groups,
                "transactionControllers": candidate.summary.transaction_controllers,
                "timers": candidate.summary.timers,
                "variables": candidate.summary.variables,
                "dataFiles": candidate.summary.data_files,
                "targets": [
                    {"scheme": t.scheme, "host": t.host, "port": t.port}
                    for t in candidate.summary.targets
                ],
            },
        )
        await audit.record(
            session,
            principal=principal,
            action="script_version.pinned",
            entity_type="script_version",
            entity_id=created.id,
            metadata={"commitSha": candidate.sha, "ref": ref},
        )
    response.status_code = 201
    return _version_response(created)
```

The two status codes are what makes re-pinning observably idempotent rather than merely harmless. `GET /api/v1/script-repos/{repo_id}/versions` pages with the cursor helper and `GET /api/v1/script-versions/{version_id}` reads one, both under `Permission.SCRIPT_READ`.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/integration/test_script_versions.py -v -m integration`
Expected: PASS — six tests.

- [ ] **Step 5: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): pin a ref to an immutable commit, idempotently"
```

---

### Task 8: Performance tests

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/performance_tests.py`, `apps/api/plimsoll_api/repositories/performance_tests.py`, `apps/api/plimsoll_api/services/performance_tests.py`, `apps/api/plimsoll_api/routers/performance_tests.py`
- Modify: `apps/api/plimsoll_api/main.py`, `apps/api/plimsoll_api/seed.py`
- Test: `apps/api/tests/integration/test_performance_tests_api.py`

**Interfaces:**
- Produces: contracts `WorkloadSpec`, `TestPlanSpec`, `SlaRuleSpec`, `TestCreate`, `TestUpdate`, `TestResponse`; service `create`, `require`, `update`, `archive`; a seeded demo test.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_performance_tests_api.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _pool_id(client: httpx.Client) -> str:
    items = client.get("/api/v1/generator-pools").json()["items"]
    return next(str(item["id"]) for item in items if item["name"] == "local-docker")


def _repo_id(client: httpx.Client, project_id: str) -> str:
    return str(
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


def _project_id(client: httpx.Client) -> str:
    return str(
        client.post(
            "/api/v1/projects",
            json={"name": "Tests", "projectKey": f"T{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )


def _body(client: httpx.Client, project_id: str) -> dict[str, object]:
    return {
        "name": "Checkout peak",
        "configuration": {
            "virtualUsers": 200,
            "durationSeconds": 600,
            "rampUpSeconds": 60,
            "generatorPoolId": _pool_id(client),
        },
        "plans": [
            {"scriptRepoId": _repo_id(client, project_id), "pinnedRef": "main",
             "virtualUsers": 200, "executionOrder": 1}
        ],
        "slaRules": [
            {"name": "p95 under 800ms", "metric": "p95", "operator": "lt",
             "threshold": 800, "unit": "ms", "severity": "ERROR"}
        ],
    }


def test_a_test_is_created_with_its_plans_and_rules(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    response = admin_client.post(f"/api/v1/projects/{project_id}/tests", json=_body(admin_client, project_id))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["version"] == 1
    assert len(body["plans"]) == 1
    assert body["slaRules"][0]["metric"] == "p95"


def test_a_ramp_longer_than_the_duration_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["configuration"]["rampUpSeconds"] = 1200
    response = admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body)
    assert response.status_code == 422


def test_an_unknown_sla_metric_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["slaRules"][0]["metric"] = "vibes"
    assert admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 422


def test_an_update_replaces_plans_and_rules_atomically(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests", json=_body(admin_client, project_id)
    ).json()
    updated = admin_client.patch(
        f"/api/v1/tests/{created['id']}",
        json={"version": created["version"], "slaRules": [], "name": "Renamed"},
    )
    assert updated.status_code == 200
    assert updated.json()["slaRules"] == []
    assert updated.json()["version"] == created["version"] + 1
    assert len(updated.json()["plans"]) == 1


def test_a_stale_version_conflicts(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests", json=_body(admin_client, project_id)
    ).json()
    admin_client.patch(f"/api/v1/tests/{created['id']}", json={"version": created["version"], "name": "First"})
    stale = admin_client.patch(
        f"/api/v1/tests/{created['id']}", json={"version": created["version"], "name": "Second"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONFLICT"


def test_a_test_is_archived(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests", json=_body(admin_client, project_id)
    ).json()
    assert admin_client.delete(f"/api/v1/tests/{created['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/tests/{created['id']}").json()["status"] == "ARCHIVED"


def test_a_viewer_cannot_create_one(admin_client: httpx.Client, viewer_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    assert viewer_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Expected: FAIL — `404` from the project-scoped tests path.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/performance_tests.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlaMetric(StrEnum):
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"
    AVG = "avg"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"


class SlaOperator(StrEnum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class SlaSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class WorkloadSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    virtual_users: int = Field(ge=1, alias="virtualUsers", serialization_alias="virtualUsers")
    duration_seconds: int = Field(
        ge=1, alias="durationSeconds", serialization_alias="durationSeconds"
    )
    ramp_up_seconds: int = Field(
        default=0, ge=0, alias="rampUpSeconds", serialization_alias="rampUpSeconds"
    )
    generator_pool_id: uuid.UUID = Field(
        alias="generatorPoolId", serialization_alias="generatorPoolId"
    )

    @model_validator(mode="after")
    def _ramp_fits(self) -> WorkloadSpec:
        if self.ramp_up_seconds > self.duration_seconds:
            raise ValueError("rampUpSeconds cannot exceed durationSeconds")
        return self


class TestPlanSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    script_repo_id: uuid.UUID = Field(alias="scriptRepoId", serialization_alias="scriptRepoId")
    # NULL means the repository's default ref; a SHA pins exactly.
    pinned_ref: str | None = Field(
        default=None, max_length=255, alias="pinnedRef", serialization_alias="pinnedRef"
    )
    virtual_users: int = Field(
        default=1, ge=1, alias="virtualUsers", serialization_alias="virtualUsers"
    )
    execution_order: int = Field(
        default=1, ge=1, alias="executionOrder", serialization_alias="executionOrder"
    )


class SlaRuleSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    name: str = Field(min_length=1, max_length=255)
    metric: SlaMetric
    entity: str | None = None
    operator: SlaOperator
    threshold: float
    unit: str | None = Field(default=None, max_length=50)
    severity: SlaSeverity = SlaSeverity.ERROR
    enabled: bool = True


class TestCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    configuration: WorkloadSpec
    plans: list[TestPlanSpec] = Field(min_length=1)
    sla_rules: list[SlaRuleSpec] = Field(default_factory=list, alias="slaRules")
    tags: list[str] = Field(default_factory=list)


class TestUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Required: an edit without the version it is based on cannot be checked for
    # a concurrent change.
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    configuration: WorkloadSpec | None = None
    plans: list[TestPlanSpec] | None = None
    sla_rules: list[SlaRuleSpec] | None = Field(default=None, alias="slaRules")
    tags: list[str] | None = None


class TestResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    name: str
    description: str | None
    status: str
    configuration: WorkloadSpec
    plans: list[TestPlanSpec]
    sla_rules: list[SlaRuleSpec] = Field(serialization_alias="slaRules")
    tags: list[str]
    version: int
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
```

- [ ] **Step 4: Write the repository and service**

`repositories/performance_tests.py` holds the document across three tables. `insert`, `get`, and `list_page_for_project` follow `repositories/projects.py` with
`COLUMNS = "id, project_id, name, description, status, configuration, version, tags, created_at, updated_at"` and `configuration` bound through `CAST(:configuration AS jsonb)`. The three that are not boilerplate:

```python
async def update_document(
    session: AsyncSession,
    test_id: uuid.UUID,
    expected_version: int,
    changes: dict[str, Any],
) -> sa.Row[Any] | None:
    """No row means the version moved under us: someone else edited first."""
    assignments = "".join(f"{column} = :{column}, " for column in changes)
    return (
        await session.execute(
            sa.text(
                f"UPDATE performance_tests SET {assignments}"
                "version = version + 1, updated_at = now() "
                "WHERE id = :id AND version = :expected_version "
                f"RETURNING {COLUMNS}"
            ),
            {**changes, "id": test_id, "expected_version": expected_version},
        )
    ).first()


async def replace_plans(
    session: AsyncSession, *, org_id: uuid.UUID, test_id: uuid.UUID, plans: list[TestPlanSpec]
) -> None:
    """Delete and insert inside the caller's transaction, so a reader never
    sees the test with half its plans."""
    await session.execute(
        sa.text("DELETE FROM performance_test_plans WHERE performance_test_id = :test"),
        {"test": test_id},
    )
    for plan in plans:
        await session.execute(
            sa.text(
                "INSERT INTO performance_test_plans "
                "(id, organization_id, performance_test_id, script_repo_id, pinned_ref, "
                " virtual_users, execution_order) "
                "VALUES (:id, :org, :test, :repo, :ref, :users, :order)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "test": test_id,
                "repo": plan.script_repo_id,
                "ref": plan.pinned_ref,
                "users": plan.virtual_users,
                "order": plan.execution_order,
            },
        )


async def replace_sla_rules(
    session: AsyncSession, *, org_id: uuid.UUID, test_id: uuid.UUID, rules: list[SlaRuleSpec]
) -> None:
    await session.execute(
        sa.text("DELETE FROM sla_rules WHERE performance_test_id = :test"), {"test": test_id}
    )
    for rule in rules:
        await session.execute(
            sa.text(
                "INSERT INTO sla_rules "
                "(id, organization_id, performance_test_id, name, metric, entity, operator, "
                " threshold, unit, severity, enabled) "
                "VALUES (:id, :org, :test, :name, :metric, :entity, :operator, :threshold, "
                ":unit, :severity, :enabled)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "test": test_id,
                "name": rule.name,
                "metric": str(rule.metric),
                "entity": rule.entity,
                "operator": str(rule.operator),
                "threshold": rule.threshold,
                "unit": rule.unit,
                "severity": str(rule.severity),
                "enabled": rule.enabled,
            },
        )


async def plans_for(session: AsyncSession, test_id: uuid.UUID) -> list[sa.Row[Any]]:
    result = await session.execute(
        sa.text(
            "SELECT script_repo_id, pinned_ref, virtual_users, execution_order "
            "FROM performance_test_plans WHERE performance_test_id = :test "
            "ORDER BY execution_order"
        ),
        {"test": test_id},
    )
    return list(result.all())


async def sla_rules_for(session: AsyncSession, test_id: uuid.UUID) -> list[sa.Row[Any]]:
    result = await session.execute(
        sa.text(
            "SELECT name, metric, entity, operator, threshold, unit, severity, enabled "
            "FROM sla_rules WHERE performance_test_id = :test ORDER BY name"
        ),
        {"test": test_id},
    )
    return list(result.all())
```

`services/performance_tests.py`:

- `create`: `projects.require` first (404 outside the organisation); validate every `script_repo_id` exists and every `generator_pool_id` exists, collecting **all** offending identifiers into one `VALIDATION_FAILED` with `details`; insert the test, then plans, then rules; audit `test.created`.
- `require`: 404, and assemble the full document.
- `update`: `update_document` with the client's `version`; a `None` result raises `CONFLICT` with the message "This test was modified by someone else; reload and reapply your change."; `plans` and `sla_rules` are replaced only when present in the body, so omitting them leaves them alone while sending `[]` clears them; audit `test.updated`.
- `archive`: status `ARCHIVED`, audit `test.deleted`.

`routers/performance_tests.py`: `POST`/`GET /api/v1/projects/{project_id}/tests`, `GET`/`PATCH`/`DELETE /api/v1/tests/{test_id}`, reads `Permission.TEST_READ`, writes `Permission.TEST_WRITE`. Register in `main.py`.

- [ ] **Step 5: Seed the demo test**

Extend `seed.py` to insert one `performance_tests` row named `Demo checkout test` against the seeded project, pool, and script repo, with 20 virtual users, 300 seconds, a 60-second ramp, one plan row, and one SLA rule (`p95 < 800`). Guard it with `NOT EXISTS` on the name, like the script repo, so re-running stays a no-op.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_performance_tests_api.py apps/api/tests/integration/test_seed.py -v -m integration`
Expected: PASS — seven test-API tests and the seed suite.

- [ ] **Step 7: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): performance tests carrying their plans and SLA rules"
```

---

### Task 9: Preflight

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/validation.py`, `apps/api/plimsoll_api/services/preflight.py`
- Modify: `apps/api/plimsoll_api/routers/performance_tests.py`
- Test: `apps/api/tests/integration/test_preflight.py`

**Interfaces:**
- Consumes: `git.client`, `plans.jmx`, `target_policy.matches_allowlist`, `target_policy.current_policy`, `pools.capacity_for`, `credentials.secret_named`.
- Produces: contracts `CheckStatus`, `Check`, `PreflightReport`; `preflight.run(...) -> PreflightReport`; `POST /api/v1/tests/{id}/validate`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_preflight.py`:

```python
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _checks(body: dict[str, object]) -> dict[str, str]:
    return {check["code"]: check["status"] for check in body["checks"]}


def test_the_seeded_demo_test_passes_preflight(admin_client: httpx.Client) -> None:
    tests = admin_client.get("/api/v1/projects").json()["items"]
    project = next(item for item in tests if item["projectKey"] == "DEMO")
    demo = next(
        item
        for item in admin_client.get(f"/api/v1/projects/{project['id']}/tests").json()["items"]
        if item["name"] == "Demo checkout test"
    )
    response = admin_client.post(f"/api/v1/tests/{demo['id']}/validate")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body["checks"]
    assert _checks(body)["TARGET_ALLOWED"] == "PASS"
    assert _checks(body)["CAPACITY"] == "PASS"


def test_a_target_outside_the_allowlist_fails_that_check(admin_client: httpx.Client) -> None:
    """The plan's ${API_HOST} resolves from the organisation's variables."""
    admin_client.post(
        "/api/v1/credentials",
        json={"name": "API_HOST", "kind": "VARIABLE", "secret": "api.elsewhere.invalid"},
    )
    try:
        project = next(
            item for item in admin_client.get("/api/v1/projects").json()["items"]
            if item["projectKey"] == "DEMO"
        )
        demo = next(
            item
            for item in admin_client.get(f"/api/v1/projects/{project['id']}/tests").json()["items"]
            if item["name"] == "Demo checkout test"
        )
        body = admin_client.post(f"/api/v1/tests/{demo['id']}/validate").json()
        assert body["ok"] is False
        assert _checks(body)["TARGET_ALLOWED"] == "FAIL"
        detail = next(c for c in body["checks"] if c["code"] == "TARGET_ALLOWED")
        assert "api.elsewhere.invalid" in detail["detail"]
    finally:
        credentials = admin_client.get("/api/v1/credentials").json()["items"]
        api_host = next(c for c in credentials if c["name"] == "API_HOST")
        admin_client.delete(f"/api/v1/credentials/{api_host['id']}")


def test_every_failing_check_is_reported_at_once(admin_client: httpx.Client) -> None:
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Preflight", "projectKey": f"F{uuid.uuid4().hex[:8].upper()}"},
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
        for item in admin_client.get("/api/v1/generator-pools").json()["items"]
        if item["name"] == "local-docker"
    )
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Too big and unresolvable",
            "configuration": {
                "virtualUsers": 100_000,
                "durationSeconds": 60,
                "rampUpSeconds": 10,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 100_000, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()

    body = admin_client.post(f"/api/v1/tests/{created['id']}/validate").json()
    assert body["ok"] is False
    statuses = _checks(body)
    assert statuses["SCRIPT_REF"] == "FAIL"
    assert statuses["CAPACITY"] == "FAIL"
    # Every check is reported, including the ones that could not run.
    assert set(statuses) == {
        "TEST_STRUCTURE", "SCRIPT_REF", "PLAN_PARSES",
        "VARIABLES_PRESENT", "TARGET_ALLOWED", "CAPACITY",
    }


def test_an_empty_allowlist_fails_the_target_check(admin_client: httpx.Client) -> None:
    admin_client.put("/api/v1/target-policy", json={"allowlist": []})
    try:
        project = next(
            item for item in admin_client.get("/api/v1/projects").json()["items"]
            if item["projectKey"] == "DEMO"
        )
        demo = next(
            item
            for item in admin_client.get(f"/api/v1/projects/{project['id']}/tests").json()["items"]
            if item["name"] == "Demo checkout test"
        )
        body = admin_client.post(f"/api/v1/tests/{demo['id']}/validate").json()
        assert _checks(body)["TARGET_ALLOWED"] == "FAIL"
    finally:
        admin_client.put("/api/v1/target-policy", json={"allowlist": ["demo-target"]})


def test_a_viewer_cannot_validate(admin_client: httpx.Client, viewer_client: httpx.Client) -> None:
    project = next(
        item for item in admin_client.get("/api/v1/projects").json()["items"]
        if item["projectKey"] == "DEMO"
    )
    demo = next(
        item
        for item in admin_client.get(f"/api/v1/projects/{project['id']}/tests").json()["items"]
        if item["name"] == "Demo checkout test"
    )
    assert viewer_client.post(f"/api/v1/tests/{demo['id']}/validate").status_code == 403
```

- [ ] **Step 2: Run it to make sure it fails**

Expected: FAIL — `404` from the validate path.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/validation.py`:

```python
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    # The check could not run because something it depends on failed. Reported
    # rather than omitted, so the caller sees the whole picture at once.
    SKIPPED = "SKIPPED"


class Check(BaseModel):
    code: str
    status: CheckStatus
    detail: str


class PreflightReport(BaseModel):
    ok: bool
    checks: list[Check]
```

- [ ] **Step 4: Write the service**

`apps/api/plimsoll_api/services/preflight.py` runs all six checks and never stops at the first failure:

```python
"""Every check runs, and every result is reported.

Validation that refuses to tell you the second problem until you fix the first
is what v0.1's definition of done rules out. A check whose input is missing
reports SKIPPED rather than vanishing from the list.
"""
```

The inputs are gathered in one transaction and handed to a pure-ish runner:

```python
@dataclass(frozen=True)
class PlanInput:
    repo_name: str
    access: GitAccess
    plan_path: str
    ref: str
    virtual_users: int


@dataclass(frozen=True)
class PreflightInput:
    requested_users: int
    plans: list[PlanInput]
    allowlist: list[str]
    # Variable name -> stored value, for every VARIABLE credential in the
    # organisation. Values are used to resolve a target host and nothing else.
    variables: dict[str, str]
    free_capacity: int


async def run(inputs: PreflightInput) -> PreflightReport:
    checks: list[Check] = []

    # 1. Structure.
    declared = sum(plan.virtual_users for plan in inputs.plans)
    if not inputs.plans:
        checks.append(Check(code="TEST_STRUCTURE", status=CheckStatus.FAIL,
                            detail="The test has no plan to execute."))
    elif declared != inputs.requested_users:
        checks.append(Check(
            code="TEST_STRUCTURE", status=CheckStatus.FAIL,
            detail=f"The plans allocate {declared} virtual users; the workload asks for "
                   f"{inputs.requested_users}."))
    else:
        checks.append(Check(code="TEST_STRUCTURE", status=CheckStatus.PASS,
                            detail=f"{len(inputs.plans)} plan(s), {declared} virtual users."))

    # 2. Every ref resolves. Failures accumulate; the loop does not stop.
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for plan in inputs.plans:
        try:
            resolution = await resolve_ref(plan.access, plan.ref)
        except GitError as exc:
            unresolved.append(f"{plan.repo_name} at {plan.ref}: {exc}")
        else:
            resolved[plan.repo_name] = resolution.sha
    checks.append(
        Check(code="SCRIPT_REF", status=CheckStatus.FAIL, detail="; ".join(unresolved))
        if unresolved
        else Check(code="SCRIPT_REF", status=CheckStatus.PASS,
                   detail=", ".join(f"{name}@{sha[:8]}" for name, sha in resolved.items()))
    )

    # 3-5. The plan-derived checks share one fetch each.
    summaries: list[PlanSummary] = []
    parse_failures: list[str] = []
    for plan in inputs.plans:
        sha = resolved.get(plan.repo_name)
        if sha is None:
            continue
        try:
            async with fetch_plan(plan.access, sha, plan.plan_path) as root:
                summaries.append(parse_plan((root / plan.plan_path).read_text(errors="replace")))
        except (GitError, OSError, PlanParseError) as exc:
            parse_failures.append(f"{plan.repo_name}: {exc}")

    if unresolved:
        checks.append(Check(code="PLAN_PARSES", status=CheckStatus.SKIPPED,
                            detail="Not attempted: a ref did not resolve."))
    elif parse_failures:
        checks.append(Check(code="PLAN_PARSES", status=CheckStatus.FAIL,
                            detail="; ".join(parse_failures)))
    else:
        checks.append(Check(code="PLAN_PARSES", status=CheckStatus.PASS,
                            detail=f"{len(summaries)} plan(s) parsed."))

    checks.append(_variables_check(summaries, inputs, blocked=bool(unresolved or parse_failures)))
    checks.append(_targets_check(summaries, inputs, blocked=bool(unresolved or parse_failures)))

    # 6. Capacity.
    checks.append(
        Check(code="CAPACITY", status=CheckStatus.PASS,
              detail=f"{inputs.requested_users} of {inputs.free_capacity} available.")
        if inputs.requested_users <= inputs.free_capacity
        else Check(code="CAPACITY", status=CheckStatus.FAIL,
                   detail=f"The test asks for {inputs.requested_users} virtual users; the pool "
                          f"has {inputs.free_capacity} available.")
    )

    return PreflightReport(
        ok=all(check.status is CheckStatus.PASS for check in checks), checks=checks
    )
```

`_variables_check` returns `SKIPPED` when `blocked`, otherwise fails naming every referenced variable with no stored value — existence only, because the value resolves at run start and is injected in memory.

`_targets_check` returns `SKIPPED` when `blocked`. Otherwise, for every target of every summary it resolves the host — a host of the form `${NAME}` is replaced by `inputs.variables[NAME]`, and one still unresolved fails naming the missing variable — then applies `matches_allowlist(host, inputs.allowlist)`. It fails listing **every** rejected host, and an absent policy is an empty allowlist, which permits nothing.

Both helpers append exactly one `Check`, so the report always carries all six codes: a check that could not run says so rather than disappearing from the list.

Git work happens between transactions, as in Task 6: the router opens one transaction to load the test, its plans, the repositories, their `GitAccess`, the policy, and the variables it needs; closes it; runs the Git checks; then opens a second transaction only to record the audit row.

- [ ] **Step 5: Write the endpoint**

In `routers/performance_tests.py`:

```python
@router.post(
    "/api/v1/tests/{test_id}/validate",
    response_model=PreflightReport,
    dependencies=[Depends(requires(Permission.TEST_WRITE))],
)
```

`200` always when the test exists — the report *is* the answer. `TEST_NOT_RUNNABLE` belongs to the run endpoint in S3.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make dev && uv run pytest apps/api/tests/integration/test_preflight.py -v -m integration`
Expected: PASS — five tests. `test_every_failing_check_is_reported_at_once` is the one that matters: if any check is missing from the set, the service is returning early somewhere.

- [ ] **Step 7: Regenerate contracts and commit**

```bash
make contracts && make lint && make typecheck && make test
git add -A
git commit -s -m "feat(api): preflight reports every failing check at once"
```

---

### Task 10: Documentation and the slice demonstration

**Files:**
- Modify: `docs/architecture/06-api.md`, `README.md`, `CLAUDE.md`
- Test: `apps/api/tests/integration/test_slice_demonstration.py`

- [ ] **Step 1: Write the demonstration test**

`apps/api/tests/integration/test_slice_demonstration.py`:

```python
"""The S2 promise, end to end: define and validate a test over HTTP."""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_a_test_can_be_defined_and_validated_from_nothing(admin_client: httpx.Client) -> None:
    project = admin_client.post(
        "/api/v1/projects",
        json={"name": "Walking skeleton", "projectKey": f"W{uuid.uuid4().hex[:8].upper()}"},
    ).json()

    credential = admin_client.post(
        "/api/v1/credentials",
        json={
            "name": f"git-{uuid.uuid4().hex[:6]}",
            "kind": "GIT_TOKEN",
            "secret": "plimsoll:plimsoll-fixture-token",
        },
    ).json()

    repo = admin_client.post(
        f"/api/v1/projects/{project['id']}/script-repos",
        json={
            "name": "Checkout",
            "repoUrl": "http://script-fixture/private/plans.git",
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
            "credentialId": credential["id"],
        },
    ).json()

    report = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify").json()
    assert report["ok"] is True, report["findings"]

    version = admin_client.post(
        f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"}
    ).json()
    assert len(version["commitSha"]) == 40

    pool_id = next(
        item["id"]
        for item in admin_client.get("/api/v1/generator-pools").json()["items"]
        if item["name"] == "local-docker"
    )
    test = admin_client.post(
        f"/api/v1/projects/{project['id']}/tests",
        json={
            "name": "Checkout peak",
            "configuration": {
                "virtualUsers": 50,
                "durationSeconds": 300,
                "rampUpSeconds": 30,
                "generatorPoolId": pool_id,
            },
            "plans": [
                {"scriptRepoId": repo["id"], "pinnedRef": version["commitSha"],
                 "virtualUsers": 50, "executionOrder": 1}
            ],
            "slaRules": [
                {"name": "p95 under 800ms", "metric": "p95", "operator": "lt",
                 "threshold": 800, "unit": "ms", "severity": "ERROR"}
            ],
        },
    ).json()

    validation = admin_client.post(f"/api/v1/tests/{test['id']}/validate").json()
    assert validation["ok"] is True, validation["checks"]
```

The `${API_HOST}` variable this plan references must resolve for `TARGET_ALLOWED` to pass, so the seed also stores a `VARIABLE` credential named `API_HOST` with the value `demo-target`. Add it beside the fixture credential in `seed.py` if Task 8's demo test did not already need it.

- [ ] **Step 2: Update the API document**

In `docs/architecture/06-api.md`, correct the surface to match what now exists:

- `POST /script-repos/{id}/verify` returns `200` with findings; `REPO_UNREACHABLE` is only for an unreachable host, credential, or ref.
- `POST /tests/{id}/validate` returns `200` with a check list.
- SLA rules and plans are part of the test document; there are no endpoints for them.
- `POST /tests/{id}/clone` and `POST /tests/{id}/schedule` are marked as later slices, not v0.1 S2.

- [ ] **Step 3: Update the README quickstart**

Add the S2 journey after the `make dev` instructions: sign in, `verify` the seeded repository, pin a commit, validate the demo test — with the exact `curl` commands, using the seeded admin credentials already documented there.

- [ ] **Step 4: Run everything**

```bash
make dev-down && make dev
make lint && make typecheck && make test && make test-int
make contracts && git diff --quiet
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "docs: the S2 journey, and the API document matches the API"
```

---

## Slice acceptance

Run every gate from a clean clone before declaring S2 done:

```bash
make dev-down
make dev
make lint
make typecheck
make test
make test-int
make contracts && git diff --quiet
```

Then confirm each criterion from the design:

- [ ] An operator creates a project, stores a Git credential, registers a script repository, and verifies it over HTTP against the fixture
- [ ] `verify` reports every finding at once, and reaches `REPO_UNREACHABLE` only when Git is genuinely unreachable
- [ ] A ref pins to a commit SHA, and re-pinning the same commit returns the same version rather than a duplicate
- [ ] A test with plans and SLA rules is created, and a concurrent `PATCH` returns `409`
- [ ] Preflight reports every failing check at once, including a target outside the allowlist and a capacity shortfall
- [ ] `PUT /target-policy` creates a new version and rejects an entry permitting loopback, link-local, or the metadata address
- [ ] A `VIEWER` is refused every write; another organisation's resources are `404`
- [ ] No API response contains a decrypted credential
- [ ] Migrations still apply and roll back cleanly, and no migration was added in this slice

## Self-review notes

- **Spec coverage.** Every section of the design maps to a task: the script fixture (1), Git access (2), plan parsing (3), the manifest contract (4), script repositories (5), `verify` (6), pinned versions (7), performance tests (8), preflight (9), documentation (10). Authorisation, audit, credentials, projects, pools, and the target policy are S2a.
- **Two places the implementer will be tempted to take a shortcut**, flagged in place: holding a database transaction open across the clone in Tasks 6, 7, and 9, and weakening the authorisation sweep in S2a Task 8 into a hand-written list. Both are called out where they arise.
- **One deliberate gap.** Plugin resolution is checked structurally only; resolving against generator images belongs to S3, and Task 6's `PluginSummary` exists so the report already carries what S3 will need.
