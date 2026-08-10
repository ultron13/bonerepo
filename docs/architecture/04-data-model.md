# 4. Data model

PostgreSQL 16 with the TimescaleDB extension on the same instance. Time-series
metrics live in hypertables; everything else is ordinary relational data.

Tables below are grouped by concern for readability, not ordered by dependency.
Alembic migrations are the source of truth for creation order once they exist.

## Tenancy

Every tenant-owned table carries `organization_id`, and isolation is enforced by
**row-level security**, not by application filters.

```sql
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON projects
    USING (organization_id = current_setting('app.current_org_id')::uuid);
```

The API sets `SET LOCAL app.current_org_id` at the start of each request
transaction, from the authenticated principal — never from a client-supplied
value. A forgotten `WHERE` clause then returns nothing instead of leaking across
tenants. Application-level scoping still exists, but as a convenience and a
clearer error message, not as the security boundary.

Policies must not depend on joins, so **every tenant-reachable table carries
`organization_id` directly** — including tables where the value is derivable
through a parent: `script_versions`, `run_generators`, `run_errors`,
`baselines`, and the `performance_metrics` hypertable. A denormalised column
costs sixteen bytes a row; a policy that joins costs a per-row subquery on
every read, and the tables without a direct policy are exactly the ones a new
query forgets. The column is stamped at insert from the parent row, never from
client input.

## Identity and access

```sql
CREATE TABLE organizations (
    id          UUID PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,
    slug        VARCHAR(100) UNIQUE NOT NULL,
    status      VARCHAR(50)  NOT NULL DEFAULT 'ACTIVE',
    settings    JSONB        NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    email            VARCHAR(320) NOT NULL,
    name             VARCHAR(255) NOT NULL,
    password_hash    TEXT,                      -- Argon2id; NULL for SSO users
    status           VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    org_role         VARCHAR(50) NOT NULL DEFAULT 'VIEWER',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, email)
);

CREATE TABLE project_members (
    project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    role        VARCHAR(50) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
```

Roles: `SUPER_ADMIN`, `ORG_ADMIN`, `PROJECT_ADMIN`, `PERFORMANCE_ENGINEER`,
`TESTER`, `VIEWER`, `SERVICE_ACCOUNT`. Project role overrides organisation role
for resources within that project.

Permissions: `project.read|write`, `script.read|write`, `test.read|write|execute|stop`,
`results.read`, `reports.create`, `admin.users`, `admin.system`.

## Projects

```sql
CREATE TABLE projects (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    name             VARCHAR(255) NOT NULL,
    project_key      VARCHAR(50)  NOT NULL,
    description      TEXT,
    environment      VARCHAR(50),
    status           VARCHAR(50) NOT NULL DEFAULT 'ACTIVE',
    tags             TEXT[] NOT NULL DEFAULT '{}',
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (organization_id, project_key)
);
```

## Secrets

```sql
CREATE TABLE credentials (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    name             VARCHAR(255) NOT NULL,
    kind             VARCHAR(50)  NOT NULL,  -- GIT_SSH_KEY | GIT_TOKEN | VARIABLE | KUBECONFIG
    ciphertext       BYTEA        NOT NULL,
    key_ref          VARCHAR(255) NOT NULL,  -- KMS / Vault key identifier
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name)
);
```

Encrypted at rest and never returned by the API — only referenced by ID.
Required from v0.1 because Git-sourced plans need repository credentials
immediately, rather than deferred to a hardening phase.

## Scripts are Git references

A script is a pointer into a repository, not an uploaded blob.

```sql
CREATE TABLE script_repos (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    project_id       UUID NOT NULL REFERENCES projects(id),
    name             VARCHAR(255) NOT NULL,
    engine           VARCHAR(50)  NOT NULL DEFAULT 'jmeter',
    repo_url         TEXT         NOT NULL,
    default_ref      VARCHAR(255) NOT NULL DEFAULT 'main',
    plan_path        TEXT         NOT NULL,   -- e.g. perf/checkout.jmx
    credential_id    UUID REFERENCES credentials(id),
    status           VARCHAR(50)  NOT NULL DEFAULT 'ACTIVE',
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE script_versions (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    script_repo_id  UUID NOT NULL REFERENCES script_repos(id),
    commit_sha      CHAR(40)     NOT NULL,
    plan_path       TEXT         NOT NULL,
    checksum        VARCHAR(128),          -- of the plan file at that commit
    commit_message  TEXT,
    committed_at    TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    metadata        JSONB,
    UNIQUE (script_repo_id, commit_sha, plan_path)
);
```

A commit SHA is immutable by construction, so the rule that every execution
references an immutable version needs no enforcement machinery — it cannot be
violated. Data files and plugin manifests beside the plan come along
automatically, which the blob-upload model handled badly. See
[ADR-0002](../adr/0002-git-sourced-scripts.md). The tester-facing contract for
these repositories — layout, `plimsoll.yaml` manifest, plugin pinning,
verification — is [script repositories](07-script-repos.md).

## Performance tests

```sql
CREATE TABLE performance_tests (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    project_id       UUID NOT NULL REFERENCES projects(id),
    name             VARCHAR(255) NOT NULL,
    description      TEXT,
    status           VARCHAR(50) NOT NULL DEFAULT 'DRAFT',
    configuration    JSONB       NOT NULL,   -- workload, duration, ramp, pool
    version          INTEGER     NOT NULL DEFAULT 1,   -- optimistic locking
    tags             TEXT[]      NOT NULL DEFAULT '{}',
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE performance_test_plans (
    id                   UUID PRIMARY KEY,
    organization_id      UUID NOT NULL REFERENCES organizations(id),
    performance_test_id  UUID NOT NULL REFERENCES performance_tests(id) ON DELETE CASCADE,
    script_repo_id       UUID NOT NULL REFERENCES script_repos(id),
    pinned_ref           VARCHAR(255),        -- NULL means use default_ref
    virtual_users        INTEGER NOT NULL DEFAULT 1,
    percentage           NUMERIC(5,2),
    execution_order      INTEGER NOT NULL
);

CREATE TABLE sla_rules (
    id                   UUID PRIMARY KEY,
    organization_id      UUID NOT NULL REFERENCES organizations(id),
    performance_test_id  UUID NOT NULL REFERENCES performance_tests(id) ON DELETE CASCADE,
    name                 VARCHAR(255) NOT NULL,
    metric               VARCHAR(255) NOT NULL,
    entity               VARCHAR(255),
    operator             VARCHAR(20)  NOT NULL,
    threshold            DOUBLE PRECISION NOT NULL,
    unit                 VARCHAR(50),
    severity             VARCHAR(50)  NOT NULL,
    enabled              BOOLEAN      NOT NULL DEFAULT TRUE
);
```

`version` gives optimistic locking, so two people editing a test concurrently
get a conflict instead of one silently overwriting the other.
`sla_rules.organization_id` closes a cross-tenant read that the column's absence
would otherwise permit.

## Generator pools and run generators

The static host registry is gone. Pools describe capacity; generators are
per-run and ephemeral.

```sql
CREATE TABLE generator_pools (
    id                     UUID PRIMARY KEY,
    organization_id        UUID NOT NULL REFERENCES organizations(id),
    name                   VARCHAR(255) NOT NULL,
    runtime                VARCHAR(50)  NOT NULL,   -- docker | kubernetes
    config                 JSONB        NOT NULL,   -- namespace, image, resources
    region                 VARCHAR(100),
    max_generators         INTEGER      NOT NULL,
    max_vus_per_generator  INTEGER      NOT NULL,
    supported_engines      TEXT[]       NOT NULL DEFAULT '{jmeter}',
    status                 VARCHAR(50)  NOT NULL DEFAULT 'ACTIVE',
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name)
);

CREATE TABLE run_generators (
    id              UUID PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id),
    run_id          UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    pool_id         UUID NOT NULL REFERENCES generator_pools(id),
    ordinal         INTEGER NOT NULL,
    external_ref    VARCHAR(255),          -- pod or container identifier
    assigned_users  INTEGER NOT NULL,
    status          VARCHAR(50) NOT NULL,
    last_heartbeat  TIMESTAMPTZ,
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    UNIQUE (run_id, ordinal)
);
```

There is deliberately **no `current_users` counter** on a pool. A mutable
counter updated outside a transaction boundary races under concurrent runs and
drifts from reality. In-flight load is derived from `run_generators` of active
runs, which cannot drift because it is the same data the orchestrator acts on.

## Runs

```sql
CREATE TABLE test_runs (
    id                      UUID PRIMARY KEY,
    organization_id         UUID NOT NULL REFERENCES organizations(id),
    project_id              UUID NOT NULL REFERENCES projects(id),
    performance_test_id     UUID NOT NULL REFERENCES performance_tests(id),
    run_number              BIGINT NOT NULL,
    status                  VARCHAR(50) NOT NULL,
    trigger_source          VARCHAR(50) NOT NULL,   -- UI | API | SCHEDULE | WEBHOOK
    started_at              TIMESTAMPTZ,
    ended_at                TIMESTAMPTZ,
    initiated_by            UUID REFERENCES users(id),
    configuration_snapshot  JSONB NOT NULL,
    summary                 JSONB,
    sla_result              VARCHAR(50),
    degraded                BOOLEAN NOT NULL DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, run_number)
);
```

`configuration_snapshot` is the reproducibility guarantee: resolved commit SHAs,
workload, generator allocation, SLA rules, and target policy version at start.
Nothing read after a run begins may come from mutable configuration.

`degraded` records that the run finished with less capacity than planned. A
degraded run may not become a baseline and is flagged in every comparison.

## Errors

```sql
CREATE TABLE run_errors (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    run_id           UUID NOT NULL REFERENCES test_runs(id) ON DELETE CASCADE,
    fingerprint   VARCHAR(128) NOT NULL,
    error_code    VARCHAR(100),
    message       TEXT,
    transaction   VARCHAR(255),
    count         BIGINT NOT NULL DEFAULT 0,
    first_seen    TIMESTAMPTZ NOT NULL,
    last_seen     TIMESTAMPTZ NOT NULL,
    sample_detail JSONB,
    UNIQUE (run_id, fingerprint)
);
```

Grouped rather than per-occurrence. Twelve thousand identical HTTP 500s are one
row with a count and one stored sample, not twelve thousand rows.

## Baselines, idempotency, audit

```sql
CREATE TABLE baselines (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    project_id       UUID NOT NULL REFERENCES projects(id),
    run_id      UUID NOT NULL REFERENCES test_runs(id),
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE idempotency_keys (
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    key              VARCHAR(255) NOT NULL,
    endpoint         VARCHAR(255) NOT NULL,
    request_hash     VARCHAR(128) NOT NULL,
    status_code      INTEGER,
    response_body    JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at       TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (organization_id, key)
);

CREATE TABLE api_keys (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL REFERENCES organizations(id),
    name             VARCHAR(255) NOT NULL,
    key_hash         VARCHAR(128) NOT NULL UNIQUE,  -- SHA-256; raw key never stored
    prefix           VARCHAR(20)  NOT NULL,         -- plim_live_ / plim_test_
    scopes           TEXT[]       NOT NULL,
    last_used_at     TIMESTAMPTZ,
    expires_at       TIMESTAMPTZ,
    revoked_at       TIMESTAMPTZ,
    created_by       UUID REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_logs (
    id               UUID PRIMARY KEY,
    organization_id  UUID NOT NULL,
    user_id          UUID,
    api_key_id       UUID,
    action           VARCHAR(255) NOT NULL,
    entity_type      VARCHAR(100),
    entity_id        UUID,
    ip_address       INET,
    metadata         JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`idempotency_keys` makes the guarantee promised by `Idempotency-Key` real: a
retried CI request returns the original response rather than starting a second
run. A key replayed with a *different* `request_hash` is a client error, not a
cache hit.

Audited actions: login, logout, project changes, script-repo and credential
changes, test modification, run start and stop, target-policy changes and
overrides, user and role changes, API key creation and revocation.

## Retention

| Data | Default | Configurable |
| --- | --- | --- |
| Raw JTL artifacts | 7 days | Per organisation |
| 5-second aggregates | 7 days | Per organisation |
| 1-minute aggregates | 180 days | Per organisation |
| 5-minute aggregates | 2 years | Per organisation |
| Run summaries | 2 years | Per organisation |
| Reports | 2 years | Per organisation |
| Audit logs | 1 year | Per organisation, minimum 90 days |

Enforced by TimescaleDB retention policies and an object-storage lifecycle rule.

## Migrations

Alembic, always. Every migration reversible; no schema change reaches an
environment by hand. CI applies migrations against a seeded database and rolls
them back again, so an irreversible migration fails review rather than
production.
