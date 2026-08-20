# Plimsoll — repository guide

Plimsoll is an open-source **performance-testing control plane**. It orchestrates
Apache JMeter across many containerised load generators, merges their results
correctly, and gates CI/CD pipelines on SLA outcomes.

> **Status: pre-alpha.** The v0.1 backend is complete: the control plane
> defines and validates tests, executes them across generator containers running
> JMeter, merges HDR sketches to report correct percentiles, groups errors,
> evaluates SLA rules, streams a run live over WebSocket, and hands back the raw
> artifacts. The web interface covers watching a run and reading its results;
> **creating** projects, repositories, and tests is still API-only. Read
> `docs/architecture/01-overview.md` before writing any code.

The full specification lives in `docs/`. This file is only the working guide —
keep it short. Do not paste product specification into it.

## Repository layout

```
apps/
  api/            FastAPI control plane (HTTP + WebSocket)
  web/            Next.js frontend
  worker/         Execution orchestrator, metrics ingestion, reporting
  agent/          plimsoll-agent — runs inside every generator container
packages/
  contracts/      Pydantic models + generated TypeScript types
  executor-sdk/   Executor plugin interface
images/
  generator/      Container image: JMeter + agent
  demo-target/    Sample system under test for the seeded demo
infrastructure/
  docker/ kubernetes/ terraform/
docs/
  architecture/   The specification
  adr/            Architecture decision records
```

## Commands

| Command | Does |
| --- | --- |
| `make dev` | Start the control plane, migrate, seed the demo project |
| `make dev-down` | Stop and remove the local stack |
| `make test` | Unit tests — `pytest` and `vitest` |
| `make test-int` | Integration tests (requires `make dev`) |
| `make e2e` | Playwright end-to-end suite |
| `make lint` | `ruff`, `eslint`, `prettier` |
| `make typecheck` | `mypy` and `tsc --noEmit` |
| `make migrate` | `alembic upgrade head` |
| `make revision m="..."` | Create a new Alembic migration |

`make dev` must stay a single command that works on a clean machine with only
Docker installed. Treat anything that breaks that as a release blocker.

## Conventions

- Python 3.12+, FastAPI, SQLAlchemy 2.0 style, Pydantic v2, Alembic. Format with
  `ruff format`; lint with `ruff`.
- TypeScript strict mode. Server state via TanStack Query — never Redux.
- Timestamps are `TIMESTAMPTZ`, stored UTC, ISO-8601 at the boundary. Localise
  only at render time.
- Database access goes through a repository; API responses are DTOs. Never
  serialise an ORM model straight to the client.
- No user-facing string literals inside business logic.
- Tests accompany behaviour changes in the same pull request.

## Invariants — do not break these

These encode the decisions in `docs/adr/`. Violating one is a correctness bug,
not a style preference.

1. **Virtual users never run in the API process.** The API enqueues work; the
   worker orchestrates; generator containers execute.
2. **Never average percentiles across generators.** Merge HDR histogram sketches
   and compute percentiles once from the merged histogram. See ADR-0004.
3. **Every run pins immutable inputs.** `configuration_snapshot` records resolved
   commit SHAs, workload, allocation, and SLA rules. A branch that moves
   mid-run must not change what executes.
4. **Every query is organisation-scoped, enforced by Postgres RLS.** Application
   filters are a convenience, not the security boundary. Never trust a
   client-supplied `organization_id`.
5. **Execution commands are idempotent.** Repeating `stop` on a stopped run
   returns `200`, not an error, and does not re-run side effects.
6. **Generator pods never restart mid-run** (`restartPolicy: Never`). A lost pod
   is capacity loss and must surface as such, never be silently rescheduled.
7. **Authorisation is enforced server-side.** Hiding a button is not access
   control.
8. **Load generation targets must pass the target policy check** before a run
   starts. The check rejects; it never merely warns, and there is no permit-all
   state. See `docs/architecture/05-security.md` and ADR-0007.

## Naming

The project is **Plimsoll**. Identifiers use `plimsoll` / `plim`:
`plimsoll-agent`, CLI `plimsoll`, API keys `plim_live_*`, env prefix
`PLIMSOLL_`, images `ghcr.io/ultron13/*`.

Product identifiers stay `plimsoll` / `plim` regardless of where the code is
hosted. Only repository and registry references follow the GitHub owner:
the canonical repository is `github.com/ultron13/bonerepo`.

Plimsoll is an independent implementation. Do not use OpenText, LoadRunner,
LRE, or VuGen naming in code, identifiers, UI copy, or documentation. Functional
comparison in prose is fine; branding and borrowed identifiers are not.
