# Design — v0.1 slice 2: Domain and Git

**Date:** 2026-08-12 · **Status:** Approved · **Slice:** 2 of 5

The second implementable slice of [v0.1](../../roadmap.md). Its goal is that a
performance engineer can define a test over HTTP and have the platform tell
them — in one response — whether it will run: the repository resolves, the plan
parses, the variables exist, the targets are permitted, and the capacity is
there.

S1 proved the tenancy boundary and put an operator behind a login. S2 gives
that operator something to configure, and gives every write an authorisation
check and an audit row.

## Scope of S2

**In:** the permission catalogue · audit logging · credentials and the key
provider · projects · generator-pool configuration · the target-policy API ·
script repositories · `verify` · pinned script versions · performance tests
with their plans and SLA rules · preflight validation · the script fixture
image.

**Out:** runs, the worker, the agent, the generator image, metrics, WebSocket,
and the web application. `POST /generator-pools/{id}/test-connection` is out
because it needs the runtime that S3 builds. Static plan analysis, the GitHub
App credential, and `Idempotency-Key` are v0.2.

**No migration.** S2 adds no table and no column: the schema landed whole in
S1, and every resource here has its table waiting. If that turns out to be
false, the gap is a bug in the S1 schema and is fixed as one.

## How S2 is split

One design, two implementation plans, because the second half is authorised by,
audited through, and validated against the first.

| Plan | Delivers |
| --- | --- |
| **S2a Domain foundations** | Permissions, audit, credentials and the key provider, projects, generator pools, the target-policy API |
| **S2b Git and tests** | The script fixture, Git access, script repos, `verify`, pinned versions, performance tests, SLA rules, preflight |

## Authorisation

Every route carries a permission check, server-side, from the first endpoint
([invariant 7](../../../CLAUDE.md)). The catalogue is the one the
[data model](../../architecture/04-data-model.md) already names:

```python
class Permission(StrEnum):
    PROJECT_READ = "project.read"
    PROJECT_WRITE = "project.write"
    SCRIPT_READ = "script.read"
    SCRIPT_WRITE = "script.write"
    TEST_READ = "test.read"
    TEST_WRITE = "test.write"
    ADMIN_SYSTEM = "admin.system"
```

`ORG_ADMIN` holds all of them; `VIEWER` holds the three reads. The map is a
constant, and `requires(Permission.PROJECT_WRITE)` is a route dependency that
raises `PERMISSION_DENIED`. The v0.3 RBAC matrix and project roles then change
the map and the resolution of a principal's permissions — not the endpoints.

**Absent and forbidden are indistinguishable across organisations.** A resource
in another organisation returns `404`, never `403`, because row-level security
returns no row and the API must not confirm that an identifier exists
elsewhere. Within the caller's organisation, a `VIEWER` attempting a write gets
`403`.

## Audit

`audit.record(session, principal, action, entity_type, entity_id, metadata)`
writes an `audit_logs` row **inside the caller's transaction**. An audited
change and the record of it commit together or not at all; an audit trail
written in a second transaction is a trail with holes in it exactly when
something went wrong.

S2 audits what the [data model](../../architecture/04-data-model.md) lists for
these resources: project changes, script-repo and credential changes,
test modification, and target-policy changes. Reads are not audited — the audit
log is a record of state change, and logging reads would bury the changes.

## Credentials and the key provider

```python
class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]: ...
    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes: ...
```

One implementation in v0.1: `LocalKeyProvider`, AES-256-GCM with a key read
from `PLIMSOLL_CREDENTIAL_KEY`, returning `key_ref` of `local:v1`. The nonce is
stored with the ciphertext. Vault, KMS, and key rotation are later providers
and later `key_ref` values behind the same protocol, which is why the reference
is stored per row rather than assumed globally.

Kinds in S2: `GIT_TOKEN`, `GIT_SSH_KEY`, and `VARIABLE`. The API accepts a
secret on create, and **never returns one**: the DTO carries id, name, kind,
and timestamps. There is no read-back endpoint, no "reveal" parameter, and no
field that could be added to one without changing this document first.

The compose file supplies a development key the way it already supplies the
development JWT secret, and the API refuses to start without one rather than
falling back to a built-in default — a default key is the same as no
encryption, discovered later.

**The API document has no `/credentials` surface.** The data model requires
credentials from v0.1 because Git-sourced plans need them immediately, but
[the API](../../architecture/06-api.md) never gave them endpoints. S2 adds the
section to that document rather than shipping an undocumented endpoint family.

## Git access

Two operations, both in `git/client.py`, both against a real `git` binary added
to the API image:

1. **`resolve_ref(repo, ref)`** — `git ls-remote` returns the commit SHA. No
   working tree, no history, one round trip.
2. **`fetch_plan(repo, sha, plan_path)`** — `git clone --filter=blob:none
   --depth 1 --sparse` followed by a sparse checkout of the plan's directory.
   Blobs arrive only for the files actually read.

Both run under a wall-clock timeout and a byte cap, into a temporary directory
removed in a `finally`. A repository that ignores the caps fails with
`REPO_UNREACHABLE` and a message saying which limit it hit.

**Credentials never reach `argv`.** A token is exported to a `GIT_ASKPASS`
helper through the environment; an SSH key is written to a `0600` file inside
the per-operation temporary directory and named by `GIT_SSH_COMMAND`. `ps` on
the API container shows a `git` command line with no secret in it, which is the
same rule the [security document](../../architecture/05-security.md) applies to
generators.

SSH host keys are accepted on first use in v0.1. That is a stated limitation,
not an oversight: pinning requires a `known_hosts` field the schema does not
have, and HTTPS tokens are the path the fixture and the documentation use.

## The script fixture

`verify` needs a Git server, and `make dev` promises a clean machine with only
Docker. `images/script-fixture` is `git-http-backend` behind nginx serving one
seeded bare repository on two paths:

| Path | Auth | Exercises |
| --- | --- | --- |
| `/public/plans.git` | none | The unauthenticated repository path |
| `/private/plans.git` | Basic | Credential decryption through to a real `git` authentication |

Smart HTTP, so partial clone and sparse checkout work as they do against a real
host. The repository contains a plan targeting the bundled `demo-target`, a
`plimsoll.yaml` declaring it, and a small CSV — the shape
[script repositories](../../architecture/07-script-repos.md) documents, so the
demo demonstrates the documented contract rather than a simplified one.

The seed adds a `GIT_TOKEN` credential holding the fixture's password and a
script repository pointing at the private path. The first thing a new
contributor can do after `make dev` is call `verify` and watch it pass.

## What `verify` checks

`verify` **reports**; it does not reject. If Git is reachable, the response is
`200` with `ok: false` and every finding at once. `REPO_UNREACHABLE` is
reserved for not reaching Git at all: bad host, bad credential, unresolvable
ref. Returning `422` for a non-conformant repository would force the
fix-one-thing-and-retry loop the [API document](../../architecture/06-api.md)
explicitly rejects.

```json
{
  "ok": false,
  "commitSha": "9f2c4a…",
  "findings": [
    { "code": "DATA_FILE_MISSING", "severity": "ERROR",
      "message": "data/users.csv is declared in plimsoll.yaml but absent at this commit.",
      "location": "plimsoll.yaml" }
  ],
  "plan": {
    "threadGroups": [ "…" ], "transactionControllers": [ "…" ], "timers": [ "…" ],
    "variables": [ "API_BASE_URL" ], "dataFiles": [ "data/users.csv" ],
    "targets": [ { "scheme": "http", "host": "demo-target", "port": 8080 } ],
    "plugins": [ { "id": "jpgc-casutg", "version": "2.10", "sha256": "…" } ]
  }
}
```

Checked in S2: the credential works, the ref resolves, the plan exists at its
path; the manifest parses and its schema `version` is supported; declared data
files exist at the ref, and files the plan references are declared or
discoverable; declared variables cover the plan's `${…}` references; and a
manifest entry carrying a *value* where a variable *name* belongs fails
verification, which is the "a manifest containing a credential fails" rule
enforced structurally.

**Plugin resolution is partial, deliberately.** S2 checks that each plugin is
pinned with both a version and a `sha256`, and reports it. Resolving a plugin
against generator images and mirrors needs the image catalogue that arrives in
S3, and claiming full coverage before then would be a lie the operator only
discovers at run time.

The `plan` summary is what the workload editor and preflight consume, and it is
stored on the pinned version so neither has to clone again.

## Plan parsing

`plans/jmx.py` parses with `defusedxml` — a `.jmx` is untrusted input, and the
stock XML parser resolves external entities. Extracted: thread groups,
transaction controllers, timers, `${…}` references, CSV data-set filenames, and
target hosts from HTTP samplers and HTTP Request Defaults.

A target host is frequently `${API_BASE_URL}` rather than a literal. `verify`
has no test context and therefore no variable values, so it reports such a host
as unresolved rather than guessing. **Preflight resolves it**, because a test
names the organisation whose `VARIABLE` credentials supply the value, and then
checks the resolved host against the allowlist. The agent's re-check
immediately before traffic remains the last word, per the
[security document](../../architecture/05-security.md).

## Pinned versions

`POST /script-repos/{id}/versions {ref}` resolves the ref and stores a
`script_versions` row: SHA, plan path, a checksum of the plan file, the commit
message and date, and the parsed plan summary in `metadata`. Re-pinning the
same `(repo, sha, plan_path)` returns the existing row rather than a duplicate,
which makes the endpoint idempotent by construction rather than by convention.

A commit SHA is immutable, so [invariant 3](../../../CLAUDE.md) — every run
pins immutable inputs — needs no enforcement machinery here beyond storing the
resolved value.

## Performance tests

A test is one document. `POST`/`PATCH /tests/{id}` carries `plans[]` and
`slaRules[]`, replaced atomically, because the documented API has no
`/sla-rules` endpoints and a rule that outlives the test it constrains is a bug
waiting to be found.

```json
{
  "name": "Checkout peak",
  "configuration": {
    "virtualUsers": 500, "durationSeconds": 600, "rampUpSeconds": 120,
    "generatorPoolId": "…"
  },
  "plans": [ { "scriptRepoId": "…", "pinnedRef": "main", "virtualUsers": 500,
               "executionOrder": 1 } ],
  "slaRules": [ { "name": "p95 under 800ms", "metric": "p95", "entity": null,
                  "operator": "lt", "threshold": 800, "unit": "ms",
                  "severity": "ERROR" } ],
  "version": 3
}
```

Workload for v0.1 is virtual users, duration, and a linear ramp, validated by
Pydantic: at least one virtual user, ramp no longer than duration. `PATCH`
requires the current `version` and returns `409 CONFLICT` on a mismatch —
optimistic locking, so two people editing a test get a conflict rather than one
silently overwriting the other.

SLA metrics are an enum (`p50`, `p90`, `p95`, `p99`, `avg`, `error_rate`,
`throughput`), operators are `lt|lte|gt|gte`, and `entity` names a transaction
or is null for the run as a whole. S4 evaluates them; S2 only has to make them
impossible to write wrongly.

## Target policy

`GET /target-policy` returns the current version. `PUT` **inserts a new version
row** rather than updating one, so a historical run can resolve what was
permitted when it ran — the rows are immutable and the run snapshots the
version.

An entry is a hostname, a domain suffix, or a CIDR. Matching is on the host as
written in the plan: exact hostname, suffix match, or CIDR containment for an
IP literal. There is deliberately **no DNS resolution at admission** — resolving
here would open exactly the repoint window the agent's second check exists to
close.

Writing an entry that would permit loopback, link-local, or the cloud metadata
address is rejected at write time. Those destinations are blocked from
generators regardless, and failing the `PUT` with a clear message beats a
policy that looks permissive and behaves otherwise.

An empty allowlist means no runs. There is no permit-all state and no
first-run exception ([ADR-0007](../../adr/0007-target-policy-rejects-by-default.md)).

## Preflight

`POST /tests/{id}/validate` reports like `verify` does: `200` with every check
and its status, never a partial answer.

| Check | Fails when |
| --- | --- |
| `TEST_STRUCTURE` | No plan, or a workload outside its bounds |
| `SCRIPT_REF` | A plan's ref does not resolve to a commit |
| `PLAN_PARSES` | The pinned plan no longer parses |
| `VARIABLES_PRESENT` | A declared variable has no stored value by that name |
| `TARGET_ALLOWED` | A resolved target host is outside the allowlist |
| `CAPACITY` | Requested users exceed pool capacity less what active runs hold |

`VARIABLES_PRESENT` checks existence, never the value — the value resolves at
run start and is injected in memory. `CAPACITY` is written as a query over
`run_generators` of active runs, which in S2 returns zero and in S3 starts
returning the truth without the query changing.

Turning a failed preflight into `TEST_NOT_RUNNABLE` belongs to the run endpoint
in S3. Validation that refuses to tell you the second problem until you fix the
first is the thing v0.1's definition of done rules out.

## Endpoints

```http
GET    /projects                     POST   /projects
GET    /projects/{id}                PATCH  /projects/{id}         DELETE /projects/{id}

GET    /credentials                  POST   /credentials           DELETE /credentials/{id}

GET    /generator-pools              POST   /generator-pools
GET    /generator-pools/{id}         PATCH  /generator-pools/{id}  DELETE /generator-pools/{id}

GET    /target-policy                PUT    /target-policy

GET    /projects/{projectId}/script-repos   POST /projects/{projectId}/script-repos
GET    /script-repos/{id}            PATCH  /script-repos/{id}     DELETE /script-repos/{id}
POST   /script-repos/{id}/verify
GET    /script-repos/{id}/versions   POST   /script-repos/{id}/versions
GET    /script-versions/{id}

GET    /projects/{projectId}/tests   POST   /projects/{projectId}/tests
GET    /tests/{id}                   PATCH  /tests/{id}            DELETE /tests/{id}
POST   /tests/{id}/validate
```

Every collection pages with the S1 cursor helper and its 50/200 default and
cap.

`DELETE` is a soft delete via `status` — `ARCHIVED` for projects, script repos,
pools, and tests — because a resource referenced by a completed run must remain
resolvable. Credentials are the exception: the table has no `status` column, so
an unreferenced credential is deleted outright and a referenced one is refused
with a new error code, `RESOURCE_IN_USE` (409), whose `details` name the script
repositories still holding it. Appending a code is what the versioning rule
permits; widening `CONFLICT` from its documented meaning is not.

`POST /tests/{id}/clone` and `POST /tests/{id}/schedule` are not in S2:
scheduling is v0.3, and clone is a convenience worth building once the test
document has settled.

## Testing

Test-first, as `CONTRIBUTING.md` requires.

Unit tests, no database: the JMX parser against fixture plans including one
with a variable-valued host; the manifest parser including the
value-where-a-name-belongs rejection; the allowlist matcher across hostname,
suffix, CIDR, and the blocked metadata address; the AES-256-GCM round trip and
a wrong-key failure; the permission map.

Integration tests against the stack, with three that are load-bearing:

> **`verify` against both fixture paths.** The anonymous path proves the Git
> client; the authenticated path proves a credential travels from
> `ciphertext` through `KeyProvider` into a successful `git` authentication.

> **A `VIEWER` receives `403` on every write endpoint**, enumerated from the
> router rather than listed by hand, so an endpoint added without a permission
> check fails the suite instead of shipping.

> **No response body contains a decrypted secret.** The suite walks every
> payload it receives for the seeded credential's value. A "reveal" field added
> later fails this test, which is the point.

Cross-organisation reads returning `404` rather than `403` extends the S1
tenancy proof to every new resource.

## Acceptance criteria

- [ ] An operator can create a project, store a Git credential, register a
      script repository, and verify it — over HTTP, against the fixture
- [ ] `verify` reports every finding at once, and reaches `REPO_UNREACHABLE`
      only when Git is genuinely unreachable
- [ ] A ref pins to a commit SHA, and re-pinning the same commit returns the
      same version rather than a duplicate
- [ ] A test with plans and SLA rules can be created, and a concurrent `PATCH`
      returns `409`
- [ ] Preflight reports every failing check at once, including a target outside
      the allowlist and a capacity shortfall
- [ ] `PUT /target-policy` creates a new version and rejects an entry
      permitting loopback, link-local, or the metadata address
- [ ] A `VIEWER` is refused every write; another organisation's resources are
      `404`
- [ ] No API response contains a decrypted credential
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`,
      and `make contracts` all pass, the last leaving the tree clean

## Decisions taken in this design

Recorded because they were not in the specification before now.

1. **`verify` and `validate` report rather than reject.** Both return `200`
   with findings. The error codes stay for the cases that genuinely are
   errors — unreachable Git, and starting a run that cannot run.
2. **Plans and SLA rules are part of the test document.** The documented API
   has no endpoints for them, and atomic replacement under the `version` guard
   is what makes concurrent editing safe.
3. **Generator-pool configuration lands in S2**, so preflight's capacity check
   is real. S3 implements the runtime behind it, including `test-connection`.
4. **The script fixture is a container in the stack.** `verify` cannot be
   demonstrated or tested without a Git server, and depending on a public host
   would put the internet on the critical path of `make dev` and CI.
5. **Target-policy entries are validated at write time** against loopback,
   link-local, and metadata addresses, rather than only at run time.
6. **Variable-valued target hosts are resolved at preflight, not at verify.**
   `verify` has no test context and would have to guess; preflight has the
   organisation's variables and can check the real host.
7. **Plugin resolution is checked structurally in S2** and resolved against
   images in S3, because the image catalogue does not exist yet.
8. **Two additions to the documented API**: a `/credentials` section, which the
   data model requires from v0.1 but the API document never gave endpoints, and
   the error code `RESOURCE_IN_USE`. Both land in
   [the API document](../../architecture/06-api.md) in this slice, because an
   endpoint family that exists only in code is how documentation starts lying.

None of these contradicts an ADR. Items 1 and 2 shape the API surface later
slices build on, so they are the ones worth disagreeing with now.
