# Plimsoll documentation

## Architecture

Read in order the first time.

| | |
| --- | --- |
| [1. Overview](architecture/01-overview.md) | What the system is, the two planes, components, principles |
| [2. Execution plane](architecture/02-execution-plane.md) | Runtimes, agent, workload model, run lifecycle, failure handling |
| [3. Metrics pipeline](architecture/03-metrics-pipeline.md) | JTL → histogram → merge → dashboard, and why percentiles are hard |
| [4. Data model](architecture/04-data-model.md) | Schema, tenancy, retention, migrations |
| [5. Security](architecture/05-security.md) | Threat model, target policy, auth, secrets, isolation |
| [6. API](architecture/06-api.md) | REST and WebSocket surface, idempotency, CI integration |
| [7. Script repositories](architecture/07-script-repos.md) | The tester-owned repo contract: layout, `plimsoll.yaml`, plugins, verify |

## Decision records

Why things are the way they are. Disagreeing with one is a legitimate way to
open a discussion.

| | |
| --- | --- |
| [ADR-0001](adr/0001-modular-monolith.md) | Modular monolith, not microservices |
| [ADR-0002](adr/0002-git-sourced-scripts.md) | Test plans come from Git, not uploads |
| [ADR-0003](adr/0003-kubernetes-native-generators.md) | Ephemeral containers with a pluggable runtime |
| [ADR-0004](adr/0004-hdr-histogram-metric-merging.md) | Merge histograms; never average percentiles |
| [ADR-0005](adr/0005-redis-streams-over-rabbitmq.md) | Redis Streams for v0.1, behind an interface |
| [ADR-0006](adr/0006-jmeter-first-executor.md) | Orchestrate JMeter; build no engine |

## Planning

- [Roadmap](roadmap.md) — scope per release
- [Design record](superpowers/specs/) — the design conversation this structure came from

## Contributing

[CONTRIBUTING.md](../CONTRIBUTING.md) · [SECURITY.md](../SECURITY.md) ·
[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)

## Conventions in these documents

- Diagrams are Mermaid, so they render on GitHub and stay diffable.
- SQL is illustrative of intent. Alembic migrations are the source of truth once
  they exist.
- Where a decision has a real cost, the cost is stated. A document that only
  lists advantages is marketing, not design.
