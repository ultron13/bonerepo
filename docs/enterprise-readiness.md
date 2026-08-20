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

### 2. The audit log cannot be read — closed

**Fixed:** `GET /api/v1/audit-logs`, keyset paginated newest-first and
filterable by action, entity, and user. It takes `ADMIN_SYSTEM` rather than a
read permission, because the trail names people. Two indexes match how it is
queried; an `EXPLAIN` confirms an index scan with no sort.

### 3. A run did not survive the worker provisioning it — closed

The stated gap was that the worker ignored `SIGTERM`. That was true and is
fixed, but chasing it found three worse faults behind it, all on the path a
Kubernetes rollout exercises on every deploy:

* Containers created but not yet recorded were invisible: never reaped, and
  colliding by name with the next attempt.
* An `ALLOCATING` run was never re-provisioned, so it waited for ever while its
  containers ran unattended.
* A generator that survived into a recovered run sat at `READY`-or-beyond and
  the run refused to start, because the check demanded exactly `READY`.

**Fixed:** the runtime can be asked what a run has, orphans are adopted before
anything is created, provisioning is re-enterable, and ready-or-beyond counts as
ready. `SIGTERM` finishes the tick in flight and exits clean.

### 4. Authentication has no throttle — closed

**Fixed:** failed sign-ins are counted per account and per source address, and
a success clears the account's count. The per-address counter does the real
work and refuses a correct password too, because working through a list until
one lands would otherwise still succeed; the per-account one is generous and
short-lived so a stranger cannot aim it at a colleague.

### 5. Nothing is observable — closed

**Fixed:** `/metrics` on the API, and a small server on the worker carrying
metrics and a liveness probe. The number an alert is built on is when the
reconciler last completed a pass. Routes are labelled by template, never by
path, and a test asserts no identifier ever reaches a label.

### 5b. Credentials for automation — closed

Not in the original list because the gap looked like "no API keys". The
schema was already there and nothing implemented it. **Fixed:** scoped keys,
hashed, shown once, revocable, with `last_used_at` so a forgotten pipeline can
be told from a live one. Issuing and revoking take `ADMIN_SYSTEM` — the
enumeration test caught that both routes had no guard, and the delete route
would have let any viewer stop every pipeline in the organisation.

### 5c. Generators ran unbounded — closed

Also not in the original list, and worse than most things that were.
`GeneratorSpec` carried `memory_limit` and `cpu_limit`, the runtime honoured
them, and nothing set them. **Fixed:** a bounded default, pool-configurable
sizing, and a JVM heap that follows the container rather than a fixed `-Xmx`
that would be killed by any pool smaller than a gigabyte.

### 6. There is no production runtime

`infrastructure/kubernetes` and `infrastructure/terraform` are empty. Pools
accept `runtime: kubernetes`; the probe answers honestly that none is
implemented. ADR-0003 chose Kubernetes-native generators, and Docker is the
development runtime.

**Needs:** the `KubernetesRuntime` behind the existing `GeneratorRuntime`
protocol, and a Helm chart. This is the largest item here.

### 7. One organisation, one identity provider — half closed

**Fixed: there is now a user directory.** Until this, a user existed only if
`seed.py` wrote one, so a second person needed a hand-written `INSERT`. That
made the product single-user in practice — an evaluation by two people was not
possible without a database client. Administrators can now invite, promote,
demote, deactivate and reactivate, from the API or the browser.

Chasing it found the fault that mattered more. Deactivation was assumed to be
a matter of setting a status, and login did check it — but `refresh` did not.
A refresh token lives fourteen days and mints access tokens the whole time, so
removing somebody would have delayed their access rather than ended it. Both
locks are now in place and each was verified to catch it alone: deactivation
revokes the user's token families, and `refresh` refuses an inactive account.

The last active administrator cannot be demoted or deactivated, because an
organisation with nobody who can administer it has no way back.

**Still open:** OIDC, and organisations as a product surface — creating one,
and roles beyond `ORG_ADMIN` and `VIEWER`. The roadmap puts SSO at v0.3 and
notes it gates enterprise pilots.

### 8. Operational edges

- The worker container runs as root to reach the Docker socket. Development
  only, and commented as such, but it is the one container that does.
- No resource limits on any compose service.

## Order of work

1. ~~Audit log read API~~ — done
2. ~~Worker recovery and graceful shutdown~~ — done
3. ~~Auth rate limiting~~ — done
4. ~~Observability~~ — done
5. ~~API keys with scopes~~ — done
6. ~~Generator resource limits~~ — done
7. Kubernetes runtime and Helm chart — the largest remaining item
8. ~~User directory: invite, roles, offboarding~~ — done
9. ~~Backup and restore; `make e2e`; the web interface's missing half~~ — done
10. ~~Credential key rotation~~ — done
11. OIDC, and organisations as a product surface

Each is landed the way the rest of this repository was: a failing test first,
the change, then the gates.
