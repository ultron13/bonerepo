# ADR-0003 — Generators are ephemeral containers, with a pluggable runtime

**Status:** Accepted · **Date:** 2026-08-10

## Context

The original design treated load generators as long-lived hosts: an
administrator provisions a machine, installs an agent, creates a registration
token, and the agent registers and heartbeats. Capacity was tracked with a
mutable `current_users` counter guarded by Redis locks.

That design reimplements a scheduler. "Which host has enough free capacity, and
how do I reserve it without two runs double-booking?" is precisely the question
Kubernetes already answers, and answers better than a Redis lock will.

It also carries costs: generators are pets to maintain and patch, permanent
registration secrets sit on them waiting to be stolen, and the counter races
under concurrent runs.

## Decision

Generators are **ephemeral containers created per run**. Capacity is declared by
a `generator_pool`, and admission is delegated to the runtime.

One `GeneratorRuntime` interface, two implementations:

- `DockerRuntime` — local development and small deployments, via the Docker socket
- `KubernetesRuntime` — production, creating a `Job` with `parallelism: N`

The generator image is identical in both; only the launcher differs.

## Consequences

- No Redis capacity locks and no long-lived agent tokens in the normal path.
  Two race-prone subsystems disappear rather than being made more careful.
- Admission control is a namespace `ResourceQuota`, maintained by people who
  already maintain quotas.
- Generators are patched by rebuilding an image, not by managing fleets.
- Agent credentials are run-scoped and expire with the run.
- `restartPolicy: Never` is mandatory. A restarted generator resets virtual-user
  state and silently corrupts the run; pod loss must surface as capacity loss.
- Contributors need only Docker, because the local path does not require a
  cluster. This is a deliberate protection of the onboarding experience.
- Cost: two runtime implementations to maintain. Mitigated by keeping the
  interface small and running the Kubernetes path in CI via `kind`.
- Cost: local Docker runs do not exercise quota, eviction, or scheduling.
  `make dev-k8s` exists for work that touches those paths.
- Load generators on bare VMs outside any container runtime are not supported at
  v0.1. A BYO external-generator mode is a roadmap item.
