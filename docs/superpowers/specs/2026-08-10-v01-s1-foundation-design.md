# Design — v0.1 slice 1: Foundation

**Date:** 2026-08-10 · **Status:** Approved · **Slice:** 1 of 5

The first implementable slice of [v0.1](../../roadmap.md). Its goal is not a
feature. It is that `make dev` works on a clean machine, an operator can log
in, and the tenancy boundary is proven by a test that fails when the boundary
is removed.

## How v0.1 is decomposed

v0.1's definition of done spans four deployable units, two shared packages, two
images, and the full schema. That is too large for one design. It is built as
five slices, each with its own design, plan, and implementation cycle:

| Slice | Delivers | Demonstrable as |
| --- | --- | --- |
| **S1 Foundation** | Compose stack, contracts package, schema and RLS, auth, health | `make dev`, log in, tenancy test |
| S2 Domain and Git | Projects, credentials, script repos, `verify`, pinned versions, tests, SLA rules, target policy, preflight | Define and validate a test over HTTP |
| S3 Execution | Worker, `MessageBus`, `DockerRuntime`, generator image, agent, run state machine | JMeter runs; artifacts land in object storage |
| S4 Metrics | JTL tailing, HDR folding, merge worker, hypertable, WebSocket, grouped errors, SLA evaluation | Correctly merged percentiles, live stream |
| S5 Web | Next.js covering the v0.1 user journey | The README quickstart |

Backend slices are demonstrable over HTTP, so the UI is built once against a
settled API rather than reworked four times.

## Scope of S1

**In:** repository layout · Docker Compose stack · `packages/contracts` and the
type-generation pipeline · the complete initial migration with row-level
security · database roles · authentication · the shared API spine (error
envelope, pagination helper, request IDs, health endpoints) · the demo target
image · the seed · the Makefile.

**Out:** projects, credentials, script repositories, performance tests, runs,
generator pools, the target-policy *API*, metrics, WebSocket, the worker, the
agent, the generator image, and the web application. The target-policy *table
and seed row* are in scope, because the schema lands whole and the seed needs
one.

## Repository layout

The layout in `CLAUDE.md`, with only S1's directories populated. Directories
belonging to later slices exist and contain a README naming the slice that
fills them, so the shape is visible without stub code pretending to work.

```
apps/api/                 FastAPI control plane            S1
apps/worker/              README only                      S3
apps/agent/               README only                      S3
apps/web/                 README only                      S5
packages/contracts/       Pydantic models + generated TS   S1
packages/executor-sdk/    README only                      S3
images/demo-target/       Sample system under test         S1
images/generator/         README only                      S3
infrastructure/docker/    Compose stack                    S1
```

## Database roles and row-level security

The most important decision in this slice. Invariant 4 says tenancy is enforced
by PostgreSQL, not by application filters; the mechanism has to be right or the
guarantee is decorative.

**PostgreSQL does not apply row-level security to a table's owner, and never
applies it to a superuser.** If the API connects as the role that ran the
migrations, every policy is silently inert and the tenancy tests still pass.
Therefore:

| Role | Owns | Used by | Notes |
| --- | --- | --- | --- |
| `plimsoll_owner` | The schema | Alembic only | Never used at runtime |
| `plimsoll_app` | Nothing | API, worker | `SELECT/INSERT/UPDATE/DELETE` only; RLS applies |

Every tenant table also gets `FORCE ROW LEVEL SECURITY`, so the protection
survives a future misconfiguration that points the application at the owner
role.

### The request transaction

Each request opens one transaction and issues, before any query:

```sql
SET LOCAL app.current_org_id = '<organisation of the authenticated principal>';
```

`SET LOCAL` is transaction-scoped, so a pooled connection cannot leak the value
into the next request. The value derives from the authenticated principal only;
no code path reads it from a request body, query string, or header.

Policies read the setting with the missing-ok flag so that a query issued
without it returns nothing rather than raising:

```sql
CREATE POLICY tenant_isolation ON projects
    USING (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid);
```

`organizations` takes the same policy against its own `id`.

### The authentication bootstrap problem

Login must find a user by email *before* an organisation is known, so it cannot
run under a policy keyed on the organisation. A blanket bypass would undo the
boundary, so the exception is made narrow and explicit: a `SECURITY DEFINER`
function owned by `plimsoll_owner`, with `EXECUTE` granted to `plimsoll_app`,
returning only the columns login needs.

```sql
CREATE FUNCTION auth_lookup_user(p_email text)
RETURNS TABLE (id uuid, organization_id uuid, password_hash text,
               status text, org_role text)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
    SELECT id, organization_id, password_hash, status, org_role
    FROM users WHERE email = lower(p_email);
$$;
```

It returns no other column, and in particular nothing from any other table.
Once the principal is established, every subsequent query runs under the policy
in the normal way. API-key authentication will need the same pattern in S2;
the function is not written until then.

## Schema

One reversible `0001_initial` migration creating twenty-three tables, the
`performance_metrics` hypertable, and a policy on every tenant-reachable table.
CI applies it against a seeded database and rolls it back, so an irreversible
migration fails review.

Twenty tables come from [the data model](../../architecture/04-data-model.md)
and [the metrics pipeline](../../architecture/03-metrics-pipeline.md) as
written. Three are additions this design introduces, each closing a gap where a
documented behaviour had nowhere to live:

**`target_policies`** — the API exposes `GET`/`PUT /target-policy` and a run
snapshots `targetPolicyVersion`, but no table was ever defined. Rows are
immutable; a `PUT` inserts a new version rather than updating in place, which
is what lets a historical run resolve the policy that was in force when it ran.

```sql
CREATE TABLE target_policies (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    version          INTEGER NOT NULL,
    allowlist        JSONB NOT NULL DEFAULT '[]',  -- hostnames, suffixes, CIDRs
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, version)
);
```

**`refresh_token_families`** — [security](../../architecture/05-security.md)
promises that reusing a consumed refresh token revokes the family, which
requires somewhere to record the family and its revocation.

```sql
CREATE TABLE refresh_token_families (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    current_hash     VARCHAR(128) NOT NULL,   -- SHA-256 of the live token
    revoked_at       TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**`project_run_counters`** — the allocation mechanism for `run_number`, taken
`FOR UPDATE` inside the run-creation transaction so concurrent starts serialise
instead of racing `UNIQUE (project_id, run_number)`.

```sql
CREATE TABLE project_run_counters (
    project_id       UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    next_run_number  BIGINT NOT NULL DEFAULT 1
);
```

All three carry `organization_id` and a policy, per the rule that no tenant
table depends on a join for its protection.

## The API spine

Layering is `router → service → repository → model`, with DTOs at the boundary.
No ORM model is serialised to a client. S1 builds one vertical of it —
authentication — plus the shared machinery every later slice inherits.

- **Error envelope.** An exception handler emitting
  `{error: {code, message, details, requestId}}`, with the
  [catalogue](../../architecture/06-api.md) as an enum so a typo cannot invent
  a code. Validation failures return every problem at once.
- **Cursor pagination.** A helper encoding an opaque cursor and enforcing the
  50/200 default and cap, so S2 does not invent its own.
- **Request IDs.** Generated per request, attached to the log record and echoed
  in the error envelope.
- **Health.** All three are unauthenticated. `/healthz` reports liveness and
  checks no dependencies; `/readyz` checks PostgreSQL, Redis, and object
  storage; both sit outside `/api/v1` so probes are unaffected by API
  versioning. `/api/v1/version` reports build version and git SHA and is
  versioned with the API, because its response shape is a contract.

Structured JSON logging keyed by `requestId` from the first commit, because
retrofitting log correlation is miserable.

## Authentication

Passwords hashed with Argon2id (`argon2-cffi`). Access tokens are short-lived
JWTs; refresh tokens rotate on use, and presenting a consumed refresh token
revokes its family — the theft signal.

Access tokens live 15 minutes, refresh tokens 14 days; both are configurable
by environment variable, and the defaults are stated here so they are not
invented twice.

Browser clients receive the refresh token in an `HttpOnly`, `SameSite=Lax`
cookie, with CSRF protection on cookie-authenticated routes. Programmatic
clients use `Authorization: Bearer`. Endpoints: `POST /auth/login`,
`POST /auth/logout`, `POST /auth/refresh`, `GET /auth/me`.

S1 issues only two of the roles in the documented enum: `ORG_ADMIN` and
`VIEWER`. The roadmap's shorthand "`ADMIN` and `VIEWER`" means these; the
column keeps the full enum from the data model so later slices widen the
product surface without a migration. Authorisation is a server-side check on
every route from the first endpoint, so no route is ever written without one.

## Contracts and type generation

`packages/contracts/python` holds the Pydantic v2 models the API imports.
`make contracts` starts the API, dumps `/api/openapi.json`, and runs
`openapi-typescript` into `packages/contracts/typescript`. CI regenerates and
fails if the working tree becomes dirty, so committed types cannot drift from
the API. Python dependencies are managed with `uv` and a committed hash-pinned
`uv.lock`, satisfying the supply-chain rule in the security document.
TypeScript uses pnpm workspaces.

## The demo target and seed

`images/demo-target` is a small FastAPI application exposing `/login`,
`/browse`, `/cart`, and `/checkout` with randomised latency and a low error
rate, shaped like the workload example in the execution-plane document. A
static response would produce a degenerate distribution and make the S4
percentile demonstration meaningless.

The seed creates one organisation, an `ADMIN` and a `VIEWER` user, a demo
project, and a `target_policies` version 1 whose allowlist contains exactly the
`demo-target` hostname. That entry is what makes the first run legal under
ADR-0007 without weakening the policy.

## Commands

Every target runs inside containers, because `CONTRIBUTING.md` promises Docker
and make are the only prerequisites.

| Target | Does |
| --- | --- |
| `make dev` | Build, start the stack, migrate, seed |
| `make dev-down` | Stop and remove |
| `make test` | Unit tests, no database |
| `make test-int` | Integration tests against the running stack |
| `make lint` / `make typecheck` | `ruff` / `mypy`, and their TS equivalents |
| `make migrate` / `make revision` | Alembic |
| `make contracts` | Regenerate TypeScript types |

S1's compose file defines `postgres` (`timescale/timescaledb:2-pg16`, so the
extension is present for `CREATE EXTENSION`), `redis` with AOF enabled at
`appendfsync everysec` per ADR-0005, `minio`, `api`, and `demo-target`. The
`worker` and `web` services are added by S3 and S5 respectively, reaching the
documented six control-plane containers plus the demo target. No empty
placeholder service is committed, because a container that starts and does
nothing is indistinguishable from one that is broken.

Configuration is environment variables prefixed `PLIMSOLL_`, with a
`.env.example` committed and no secrets in the repository.

## Testing

Test-first, per `CONTRIBUTING.md`. Unit tests cover token rotation, the error
envelope, cursor encoding, and password hashing without touching a database.

The integration suite runs against the compose stack, and one test in it is
load-bearing:

> Connect as `plimsoll_app`. Seed two organisations with rows in the same
> table. Set `app.current_org_id` to the first and assert only its rows are
> visible. Repeat for the second. Then run the same query with no setting and
> assert zero rows.

If someone later points the application at the owner role, or drops a policy,
that test fails. Its inverse — that a missing setting returns nothing rather
than everything — is what proves the boundary fails closed. A second
integration test asserts `auth_lookup_user` returns only its five columns, so
the deliberate exception cannot quietly widen.

## Acceptance criteria

- [ ] `make dev` succeeds on a machine with only Docker and make, from a clean
      clone, and `make dev-down` removes everything it created
- [ ] `/healthz`, `/readyz`, and `/api/v1/version` respond correctly, and
      `/readyz` fails when a dependency is stopped
- [ ] The seeded admin can log in, refresh, call `/auth/me`, and log out
- [ ] Reusing a consumed refresh token revokes the family and rejects the next
      refresh
- [ ] The RLS isolation test and its no-setting inverse pass
- [ ] `0001_initial` applies and rolls back cleanly
- [ ] `make lint`, `make typecheck`, and `make test` pass
- [ ] `make contracts` leaves the working tree clean

## Decisions taken in this design

Recorded because they were not in the specification before now.

1. **Two database roles plus `FORCE ROW LEVEL SECURITY`.** Without a runtime
   role distinct from the owner, policies do not apply and the tests pass
   anyway — the worst kind of failure.
2. **`SECURITY DEFINER` lookup for login.** The narrowest way to resolve the
   pre-authentication chicken-and-egg problem, in preference to a role that
   bypasses row-level security wholesale.
3. **Three new tables** — `target_policies`, `refresh_token_families`,
   `project_run_counters` — each backing a behaviour already promised
   elsewhere in the specification.
4. **The demo target is a real latency-shaped service**, not a static
   response, so later slices demonstrate something true.

None of these contradicts an ADR. If any turns out to be wrong, it is cheap to
change now and expensive after S3.
