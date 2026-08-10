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
- Webhooks: `run.started`, `run.completed`, `run.failed`, `sla.failed`
- `plimsoll` CLI: `login`, `test run --wait`, `run status`, `run wait`
- `plimsoll plan lint` — plan hygiene checks, runnable as a pre-commit hook or
  CI step in the script repository ([script repositories](architecture/07-script-repos.md))
- Full audit log

Degraded runs cannot become baselines, and are flagged in every comparison.

## v0.3 — Team platform

- Full RBAC matrix, organisation and project roles
- Multi-tenancy product surface
- Scheduling: one-time, daily, weekly, cron
- Reports: executive summary, performance summary, SLA, comparison — HTML, PDF, CSV, JSON
- Notifications: email, Slack, Microsoft Teams, webhook
- Global search and saved filters
- Tags across projects, tests, and runs

## v0.4 — Beyond one engine

- A second executor — k6 or Locust — specifically to flush JMeter-shaped
  assumptions out of the `Executor` interface
- Step and spike ramp profiles via `jpgc-casutg`
- Throughput and arrival-rate workload models
- External generators: BYO hosts outside any container runtime
- Monitor plugins: Prometheus and OpenTelemetry
- Correlating transactions with target-side traces

## v1.0 — Production hardening

- OIDC and SAML
- High availability: multiple API and worker replicas, scheduler leader election
- Backup and restore, documented and tested
- Rate limiting per organisation
- Full OpenTelemetry instrumentation
- Documented upgrade path and API stability guarantee
- Kubernetes deployment via Helm chart

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

Dates are deliberately absent. This is a pre-alpha project without a delivery
team, and publishing dates it cannot hold would be worse than publishing none.
