# Enterprise readiness

What is built, what holds it back, and in what order to fix it.

Written after verifying the tree rather than reading the roadmap: every claim
below was checked against the running system on 2026-08-20.

## Verified state

| Gate | Result |
| --- | --- |
| `make dev` | Eight services from a clean machine |
| `make lint` | Clean — Python and TypeScript |
| `make typecheck` | Clean — mypy and tsc |
| `make test` | 184 unit tests |
| `make test-int` | 212 integration tests, real containers, real load |
| `make contracts` | Idempotent, tree stays clean |

A test defined over the API runs across generator containers, JMeter generates
load against the target, HDR sketches merge so percentiles are computed once,
errors group, SLA rules produce a verdict, the run streams live, and the raw
artifacts come back. All of that is exercised by the integration suite, not
asserted here.

## What stops this being enterprise ready

Ordered by what blocks adoption first, not by effort.

### 1. Nothing enforced a gate until now

There was no CI. Every check above existed only on a developer's machine, which
means the only thing preventing a regression reaching `main` was somebody
remembering. **Fixed:** `.github/workflows/ci.yml` runs the same `make` targets
on every pull request, and `security.yml` audits dependencies and scans images
weekly as well as on change.

It found three high-severity vulnerabilities in the frontend within minutes of
existing. That is the argument for it.

### 2. The audit log cannot be read

Rows are written on every mutation. There is no repository read function and no
endpoint, so the log exists as a table nobody can query. An audit log that
cannot be produced on request does not satisfy the requirement it was built for.

**Needs:** a paginated, filterable read API scoped by organisation, with the
same RLS guarantees as everything else.

### 3. The worker does not shut down

No `SIGTERM` handling. Kubernetes and `docker stop` both signal and then kill.
The reconciler converges after a restart, so nothing is corrupted — but a worker
killed mid-provision leaves containers whose rows were never written, and those
are the ones nothing reaps.

**Needs:** signal handling that stops accepting work, finishes the tick in
flight, and exits.

### 4. Authentication has no throttle

`POST /api/v1/auth/login` accepts unlimited attempts. Credential stuffing
against a deployment with a known seeded administrator is trivial.

**Needs:** per-identifier and per-address rate limiting on the auth endpoints,
and a lockout that cannot be used to lock a legitimate user out.

### 5. Nothing is observable

Structured JSON logs exist and carry a request id. There are no metrics: no
Prometheus endpoint, no OpenTelemetry, no way to alert on a worker that stopped
reconciling or a run stuck in `ALLOCATING`. The platform measures other systems
and cannot measure itself.

**Needs:** a metrics endpoint on the API and the worker, and the handful of
numbers an operator would actually page on.

### 6. There is no production runtime

`infrastructure/kubernetes` and `infrastructure/terraform` are empty. Pools
accept `runtime: kubernetes`; the probe answers honestly that none is
implemented. ADR-0003 chose Kubernetes-native generators, and Docker is the
development runtime.

**Needs:** the `KubernetesRuntime` behind the existing `GeneratorRuntime`
protocol, and a Helm chart. This is the largest item here.

### 7. One organisation, one identity provider

Row-level security is enforced on every table and the plumbing is multi-tenant,
but there is no product surface for organisations, no role beyond `ADMIN` and
`VIEWER`, no API keys, and no OIDC. The roadmap puts SSO at v0.3 and notes it
gates enterprise pilots.

### 8. Operational edges

- The worker container runs as root to reach the Docker socket. Development
  only, and commented as such, but it is the one container that does.
- No resource limits on any compose service.
- No backup or restore story for Postgres or the object store.
- `make e2e` is referenced in the README and does not exist; the browser
  journey was verified by hand.
- The web interface cannot create projects, repositories, or tests, so a first
  run still needs the terminal.

## Order of work

1. Audit log read API — smallest gap between "recorded" and "auditable"
2. Worker graceful shutdown — correctness under ordinary operations
3. Auth rate limiting — the cheapest real attack to close
4. Observability — metrics and the alerts worth having
5. Kubernetes runtime and Helm chart
6. API keys with scopes, then OIDC
7. The operational edges above

Each is landed the way the rest of this repository was: a failing test first,
the change, then the gates.
