# ADR-0005 — Redis Streams for v0.1, behind a MessageBus interface

**Status:** Accepted · **Date:** 2026-08-10

## Context

The design called for RabbitMQ as the broker and TimescaleDB as a separate
database alongside PostgreSQL. Together with Prometheus, Grafana, Loki, and
Tempo, the development environment reached twelve containers.

Two forces conflict. The architecture genuinely needs durable queuing for
metrics ingestion, execution dispatch, report generation, and notifications. But
every container in `make dev` is a tax on contribution: a heavy stack means slow
first runs, more failure modes on unfamiliar machines, and contributors who
close the tab before seeing anything work. For a pre-alpha project seeking
contributors, that cost is not hypothetical — it is the main risk.

## Decision

For v0.1:

- **Redis Streams** as the broker. Redis is already mandatory for live WebSocket
  fan-out, rate limiting, and locks. Consumer groups give at-least-once
  delivery, acknowledgements, and replay — enough for MVP volume.
- **TimescaleDB as a PostgreSQL extension** on the same instance, not a separate
  database.
- **Observability behind `--profile observability`**, off by default.

All queue access goes through a `MessageBus` interface, so RabbitMQ or Kafka
drops in without touching call sites.

Development stack: `postgres`, `redis`, `minio`, `api`, `worker`, `web` — six
containers.

## Consequences

- `make dev` starts in a reasonable time on an ordinary laptop.
- One fewer stateful service to operate, back up, and understand.
- The abstraction is validated by the fact that swapping it is a configuration
  change rather than a refactor.
- Cost: Redis Streams offers less than RabbitMQ — no topic exchanges, no dead
  letter exchanges, no per-message TTL, weaker operational tooling. Dead-letter
  handling is implemented in the consumer rather than the broker.
- Cost: Redis durability depends on AOF configuration, and a misconfigured
  instance can lose acknowledged messages. Production deployments must enable
  AOF with `appendfsync everysec` at minimum. This is documented rather than
  assumed.
- Trigger for revisiting: sustained ingestion above roughly 1 million metric
  events per minute, or a need for fan-out topologies Streams cannot express.
  At that point the `MessageBus` interface gets a RabbitMQ or Kafka
  implementation and this ADR is superseded.
- The six-container budget is a stated constraint. Adding a required service to
  `make dev` needs discussion, not just a pull request.
