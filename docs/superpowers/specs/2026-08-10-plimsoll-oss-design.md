# Design record — converting the LRE MVP spec into Plimsoll

**Date:** 2026-08-10 · **Status:** Approved · **Supersedes:** the original
`CLAUDE.md` specification (commit `fb5720e`)

This records the decisions taken when reworking a 216-section internal-style
product specification into an open-source project. The resulting documents live
in `docs/architecture/` and `docs/adr/`; this file records *why* the change was
made and what was rejected.

## Starting point

A single 75 KB `CLAUDE.md` specifying an enterprise performance-testing platform
modelled on OpenText LoadRunner Enterprise. Architecturally strong — the
control/execution plane split, immutable run snapshots, executor abstraction,
"no virtual users in the API process", and "no PostgreSQL as a metric bus" were
all correct. Unsuitable for open source as written.

## Problems identified

| # | Problem | Severity |
| --- | --- | --- |
| 1 | Titled after, and pervasively named for, a competitor's trademarked product. Identifiers `lre-agent`, `lre-mvp/`, `lre_live_*`, `lre login` carried the mark | Blocking |
| 2 | No licence, no governance, no contributor documentation | Blocking |
| 3 | Strategy collided with mature OSS engines: a bespoke HTTP engine was built first, the differentiating control plane last | Critical |
| 4 | Scope unshippable — 24 modules, 42 build steps, 28-item DoD, nothing demonstrable until step ~26 | Critical |
| 5 | No abuse controls, in a tool that is functionally a DDoS platform | Critical |
| 6 | Twelve-container development environment, hostile to contribution | High |
| 7 | 75 KB PRD in `CLAUDE.md`, injected into every agent session | High |
| 8 | Percentiles aggregated per generator, then combined — statistically invalid | High |
| 9 | `sla_rules` missing `organization_id`; no RLS; mutable `current_users` counter; no idempotency key storage despite the API promising idempotency; no optimistic locking | Medium |

## Decisions

Each is recorded as an ADR; summarised here with what was rejected.

### 1. Orchestrate JMeter — build no engine

**Chosen:** JMeter as the sole v0.1 executor, driven headless per generator.
**Rejected:** building an HTTP engine first (competes where the field is
strongest, delays the differentiator, slowest to a demo); k6 first (AGPL-3.0
constrains packaging against an Apache-2.0 project, and the target audience
already holds `.jmx` assets); both in parallel (more work than a pre-alpha
project can carry).

Accepted costs: thread-per-VU caps generators at roughly 500–2,000 VUs rather
than 5,000, so capacity is declared per pool; step and spike ramps require
`jpgc-casutg`; a JVM in every generator image. → [ADR-0006](../../adr/0006-jmeter-first-executor.md)

### 2. Apache-2.0

**Rejected:** AGPL-3.0 — network copyleft would protect against SaaS
free-riding but is banned outright at many of the enterprises this platform
targets, cutting off both users and corporate contributors. Open-core — premature
before a community exists, and costly to police from day one. MIT — no patent
grant, which matters in a space with active commercial patents.

Apache-2.0 also matches JMeter's own licence, so the executor boundary carries no
compatibility work.

### 3. Named Plimsoll

The Plimsoll line is the mark on a hull showing maximum safe load — structurally
the same idea as an SLA threshold. Distinctive, near-zero collision risk in
package registries, and gives the project a real visual identity.

**Rejected:** Loadline (clearer but more generic), Throughline (doesn't say what
it does), OpenPerf (unbrandable).

Accepted cost: obscure to most people, so the README tagline must do the
explaining from line one.

Identifiers: `plimsoll/plimsoll`, `plimsoll-agent`, CLI `plimsoll`, keys
`plim_live_*`, env `PLIMSOLL_`, images `ghcr.io/plimsoll/*`. All OpenText,
LoadRunner, LRE, and VuGen references removed from code, identifiers, UI copy,
and docs; a single attribution note in the README states non-affiliation.

### 4. Test plans come from Git

**Chosen:** a script is a repository reference; a version is a commit SHA.
**Rejected:** upload-to-object-storage, which requires engineering immutability
rather than inheriting it, separates plans from the code they test, and handles
multi-file plans badly.

Accepted cost: Git credential management moves into v0.1, so the `credentials`
table and encryption leave the hardening phase. → [ADR-0002](../../adr/0002-git-sourced-scripts.md)

### 5. Generators are ephemeral containers, runtime pluggable

**Chosen:** `GeneratorRuntime` with `DockerRuntime` (local) and
`KubernetesRuntime` (production), identical image behind both.
**Rejected:** static registered hosts with Redis capacity locks — reimplements a
scheduler, creates long-lived secrets on generators, and races under concurrent
runs. Also rejected: requiring `kind` locally, which would put a cluster between
a new contributor and their first successful run.

This deletes two subsystems rather than adding one. Accepted costs: two runtime
implementations; local Docker does not exercise quota or eviction, so
`make dev-k8s` and CI cover the Kubernetes path; no support for bare-VM
generators until v0.4. → [ADR-0003](../../adr/0003-kubernetes-native-generators.md)

### 6. Merge HDR histograms; never average percentiles

The load-bearing correctness fix. Agents fold raw JTL samples into HDR
histograms per transaction per 5-second window and ship mergeable sketches;
percentiles are derived once from the merged histogram. Every metric declares a
kind — `counter`, `gauge`, `histogram` — with a defined merge rule, and no code
path accepts a pre-computed percentile from a generator.

Accepted cost: ~0.1% bounded error instead of exact values — far better than the
unbounded, invisible error of averaging. JMeter's built-in Backend Listener
cannot be used as-is because it emits pre-aggregated percentiles; the agent
tails raw JTL instead, which also keeps it entirely in Python.
→ [ADR-0004](../../adr/0004-hdr-histogram-metric-merging.md)

### 7. Six containers, not twelve

Redis Streams replaces RabbitMQ for v0.1 behind a `MessageBus` interface;
TimescaleDB runs as a PostgreSQL extension rather than a second database;
observability moves behind an opt-in profile. Contributor onboarding is treated
as an architectural constraint, and the six-container budget is stated so that
additions require discussion. → [ADR-0005](../../adr/0005-redis-streams-over-rabbitmq.md)

### 8. Target policy, on by default

Not present in the original at all. A distributed, API-triggerable,
schedulable traffic generator is a DDoS platform; authorisation is the only
thing distinguishing a load test from an attack.

Per-organisation allowlists of hostnames, domains, and CIDR ranges. **Empty
allowlist means no runs** — there is no implicit permit-all state. Targets are
checked at run creation *and* again on the agent immediately before traffic, so
a repointed DNS record cannot slip between admission and execution. Loopback,
link-local, and cloud metadata endpoints are blocked unconditionally. The policy
version is snapshotted onto the run, and rejections and overrides are audited.

> **Assumption, flagged for review.** This posture — enforced by default,
> loosenable by the operator, never silently absent — was chosen without an
> explicit decision from the maintainer, because shipping without it would be
> unsafe. If a softer posture is wanted (warn-and-audit rather than reject), it
> is a small change to `docs/architecture/05-security.md` and the preflight
> check, but it should be a deliberate choice rather than a default.

### 9. Data model corrections

`organization_id` added to `sla_rules` · PostgreSQL RLS on
`app.current_org_id` as the tenancy boundary rather than application filters ·
`idempotency_keys` table so `Idempotency-Key` means something ·
`version` on `performance_tests` for optimistic locking ·
`current_users` counter deleted in favour of deriving in-flight load from
`run_generators` · per-transaction rows dropped from PostgreSQL at v0.1 in
favour of aggregates, grouped errors, and raw JTL in object storage ·
`degraded` flag on runs, which blocks promotion to a baseline.

### 10. Documentation restructured

The PRD leaves `CLAUDE.md` for `docs/architecture/` (six documents) and
`docs/adr/` (six records). `CLAUDE.md` becomes roughly a page: layout, commands,
conventions, and eight invariants an agent must not break.

## Scope

v0.1 is a thin end-to-end path: log in → project → connect Git repo → pin a
plan → define a test → run across N generators → live metrics → correctly merged
percentiles → SLA pass/fail. Multi-tenancy, full RBAC, scheduling, reports,
baselines, notifications, CI/CD triggers, and the audit UI are explicitly
excluded and written down as excluded.

Every architectural seam is still built — executor interface, runtime interface,
message bus, control/execution split, RLS — with one implementation behind each.

**Rejected:** a standalone distributed JMeter CLI first (defers the
differentiator, and CLI-shaped assumptions retrofit badly into a multi-user
server); layer-complete construction per the original build order (nothing
demonstrable for months, the standard way a solo-maintained project dies).

Release sequence: v0.2 regression detection and CI · v0.3 team platform ·
v0.4 second executor and external generators · v1.0 production hardening.
→ [Roadmap](../../roadmap.md)

## What was preserved

The five governing principles from the original (§198–§202) are unchanged and
now sit in [the overview](../../architecture/01-overview.md): no virtual users in
the API process, no PostgreSQL as a metric bus, immutable run snapshots,
control/execution separation, and executor abstraction before protocols. The
domain model, run state machine, workload model, SLA model, report types, and
RBAC roles all survive largely intact.

The original specification's instincts were sound. What it needed was a legal
identity, a defensible strategy, a shippable scope, and honesty about what it
costs.

## Open items

- **Contact addresses.** `CODE_OF_CONDUCT.md` carries a placeholder enforcement
  address, and `SECURITY.md` assumes GitHub private vulnerability reporting.
  Both need real values before the repository is made public.
- **GitHub organisation.** Documents assume `github.com/plimsoll/plimsoll`.
  Registry and organisation names need claiming, or the references need updating.
- **Target policy posture.** See the flagged assumption above.
