# Roadmap

Scope per release. The organising rule: **v0.1 is a thin path that works
end to end**, not a broad set of half-features. Every architectural seam gets
built, with exactly one implementation behind each.

## v0.1 — Walking skeleton

One narrow path, complete and demonstrable.

### Definition of done

A user can:

- [ ] Log in (single organisation, `ADMIN` and `VIEWER` only)
- [ ] Create a project
- [ ] Connect a Git repository and verify credentials
- [ ] Verify the repository against its `plimsoll.yaml` manifest — or inferred
      defaults for a bare `.jmx` repo ([script repositories](architecture/07-script-repos.md))
- [ ] Pin a `.jmx` plan to a resolved commit SHA
- [ ] Define a performance test — virtual users, duration, linear ramp
- [ ] Configure SLA rules
- [ ] Validate the test and see every failure at once, not one at a time
- [ ] Start a run across N generator containers
- [ ] Watch live metrics stream over WebSocket
- [ ] Stop a run cleanly, idempotently
- [ ] See results with **correctly merged** percentiles
- [ ] See grouped errors with counts and samples
- [ ] See SLA pass/fail per rule
- [ ] Download raw run artifacts

And an operator can:

- [ ] Configure a target allowlist, without which no run starts
- [ ] Create a generator pool for Docker or Kubernetes
- [ ] Run everything with `make dev` on a machine that has only Docker
- [ ] Read a published version support policy — which versions get fixes and
      for how long, as [SECURITY.md](../SECURITY.md) commits to for the first
      release

### Explicitly out of v0.1

Written down so nobody assumes otherwise: multi-tenancy, the full RBAC matrix,
scheduling, reports, baselines, notifications, CI/CD triggers, audit log UI,
requirements module, second executor, OIDC/SAML.

The database carries `organization_id` and row-level security from the first
migration — the *plumbing* for multi-tenancy is present, the product surface for
managing multiple organisations is not.

## v0.2 — Regression detection

The release that makes the platform useful in a pipeline.

- Baselines: mark a completed run as a baseline
- Run comparison with regression highlighting
- Per-transaction drill-down, backed by raw artifacts rather than a transaction table
- API keys with scopes
- CI-triggered runs with `Idempotency-Key`
- Webhooks: `run.started`, `run.completed`, `run.failed`, `sla.failed` —
  managed via the subscription API, with rotatable HMAC secrets
- GitHub App credential for script repositories; run verdicts posted back to
  the pinned commit as check runs
- Data distribution modes for CSV files — shared, partitioned, unique-per-VU
  ([execution plane](architecture/02-execution-plane.md))
- Plan static analysis in `verify`: risky JMX elements flagged, with a
  per-organisation element policy ([security](architecture/05-security.md))
- `plimsoll` CLI: `login`, `test run --wait`, `run status`, `run wait`
- `plimsoll plan lint` — plan hygiene checks, runnable as a pre-commit hook or
  CI step in the script repository and packaged as a GitHub Action
  ([script repositories](architecture/07-script-repos.md))
- Tester-facing guide: writing JMeter plans for Plimsoll
- Full audit log

Degraded runs cannot become baselines, and are flagged in every comparison.

## v0.3 — Team platform

- Full RBAC matrix, organisation and project roles
- Multi-tenancy product surface
- OIDC single sign-on — moved ahead of v1.0, because SSO gates enterprise
  pilots rather than production hardening
- Kubernetes deployment via Helm chart — the production runtime gets a
  first-class install path
- Audit-log streaming: webhook, syslog, or S3 export into the SIEMs
  enterprises already run
- Capacity-planning guide with measured generator sizing tables
- Scheduling: one-time, daily, weekly, cron
- Reports: executive summary, performance summary, SLA, comparison — HTML, PDF, CSV, JSON
- Trend views: a metric across the last N runs of a test, not just one run
  against one baseline. Slow drift is invisible in a single diff and obvious in
  a line
- Notifications: email, Slack, Microsoft Teams, webhook
- Global search and saved filters
- Tags across projects, tests, and runs

### Data governance

Enterprise pilots ask these questions during procurement, not after.

- **Per-project target allowlists**, layered under the organisation policy and
  never widening it. Ships with project roles because it is the same idea:
  a team should reach only what its project is authorised to reach
  ([ADR-0007](adr/0007-target-policy-rejects-by-default.md))
- **PII masking in artifacts.** Secret scrubbing already covers resolved
  variables, but a JTL captures request and response bodies — tokens, emails,
  customer records. Configurable masking at the agent, plus documentation
  stating plainly that raw artifacts inherit the sensitivity of the traffic
  they record
- **Organisation export and erasure** across PostgreSQL, Timescale hypertables,
  and object storage — a tested path, not a support ticket

### Adoption

- Migration guides: from LoadRunner concepts, from hand-rolled distributed
  JMeter, from hosted load-testing services
- `plimsoll import` — the helper [ADR-0002](adr/0002-git-sourced-scripts.md)
  promises for teams whose plans are not yet in Git

## v0.4 — Beyond one engine

- A second executor — k6 or Locust — specifically to flush JMeter-shaped
  assumptions out of the `Executor` interface
- Step and spike ramp profiles via `jpgc-casutg`
- Throughput and arrival-rate workload models
- External generators: BYO hosts outside any container runtime
- Monitor plugins: Prometheus and OpenTelemetry
- Correlating transactions with target-side traces

## v1.0 — Production hardening

The release where an enterprise can run Plimsoll as infrastructure rather than
as a pilot. Grouped by the question each item answers.

### Identity — "does it fit our access model?"

- SAML and LDAP (OIDC ships in v0.3)
- **SCIM provisioning.** Without automated deprovisioning, a leaver keeps
  access to a tool that generates production-scale load. This is the control
  auditors ask about
- **MFA (TOTP)** for local accounts, for deployments that run without SSO
- **Per-API-key network allowlists and default expiry.** A CI key that starts
  load tests should work from the CI network and nowhere else, and should
  expire unless deliberately renewed

### Availability — "what happens when it breaks?"

- High availability: multiple API and worker replicas, scheduler leader election
- Helm chart hardened for HA: replicas, disruption budgets, upgrade hooks
- **Backup and disaster recovery**, documented *and rehearsed*, covering
  PostgreSQL with Timescale hypertables, object storage, and Redis AOF — with
  a stated RTO and RPO rather than an implied one
- **Zero-downtime upgrades.** Migrations already must be reversible; v1.0 adds
  the expand/contract convention so a schema change is safe against a running
  control plane, and states which version skips are supported
- Rate limiting per organisation, on top of the per-principal limits already
  present
- Full OpenTelemetry instrumentation, a documented Prometheus endpoint, a
  structured log schema keyed by `requestId`, and published control-plane SLOs

### Assurance — "can our security team sign this off?"

- **SBOM and signed releases as artifacts.** The supply-chain controls in
  [security](architecture/05-security.md) become things a security team can
  verify without asking: a CycloneDX SBOM and cosign signatures published with
  every release
- **External penetration test**, scoped to what [SECURITY.md](../SECURITY.md)
  already declares in scope — tenancy isolation, target-policy bypass, the
  agent protocol, container escape
- **Air-gapped installation**: image mirroring instructions and a guarantee of
  no runtime internet dependency. The plugin rules in
  [script repositories](architecture/07-script-repos.md) exist to keep this
  achievable
- **Corporate proxy support**, documented and tested, for Git clone and the
  agent's outbound WebSocket
- Documented upgrade path and API stability guarantee

## Later

Cloud-hosted generators across regions · service and network virtualisation ·
automatic bottleneck detection · AI-assisted result analysis.

AI stays an assistant. Test execution and SLA evaluation must remain
deterministic and never depend on a model.

## What would change this plan

- If contributors arrive with deep JMeter expertise, executor work accelerates
  and v0.4 items move earlier.
- If early users need CI gating before regression detection, v0.2's CI items
  move into v0.1.
- If Redis Streams hits its limits sooner than expected, the RabbitMQ
  implementation behind `MessageBus` moves forward ([ADR-0005](adr/0005-redis-streams-over-rabbitmq.md)).
- If an enterprise pilot blocks on a specific v1.0 control — SCIM, an SBOM, a
  rehearsed restore — that item moves forward on its own. OIDC and the Helm
  chart already moved to v0.3 for exactly this reason. Procurement gates are
  not the same thing as production hardening, and mistaking one for the other
  is what leaves a good tool unadopted.

Dates are deliberately absent. This is a pre-alpha project without a delivery
team, and publishing dates it cannot hold would be worse than publishing none.
