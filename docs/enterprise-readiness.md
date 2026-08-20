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

### 6. There is no production runtime — closed

**Fixed:** `KubernetesRuntime` behind the existing protocol, and a Helm chart.
Generators are bare pods with `restartPolicy: Never` — not Jobs, not
Deployments, because both exist to survive node loss and invariant 6 says a
generator must not. Verified against a live cluster rather than rendered:
the chart applies to a real API server, and the runtime creates, finds, sizes
and removes pods on one. The RBAC boundary is asserted by asking the cluster,
not by reading the Role.

Two namespaces, so a `ResourceQuota` can cap load generation without capping
the API and a `NetworkPolicy` can deny generator egress by default.

Chasing it found the invariant-3 hole below.

### 6b. A run did not pin the pool it was started with — closed

Not in the original list, and the most serious thing found so far.
`configuration_snapshot` pinned commit SHAs, workload, allocation and SLA
rules; it did not pin the generator pool. The image, the runtime and the
sizing were read live at provision time, minutes after preflight had approved
something else — so editing a pool in that window changed what executed, and
the docstring promising "nothing read after it starts comes from mutable
configuration" was false.

The tell was `_pool_settings` reading `snapshot.get("image")`, which
`build_snapshot` never wrote: the fourth instance of a field that exists with
nothing wiring it. Switching a pool's runtime mid-run was the worst case —
teardown would look in the runtime the pool names now, find nothing, and leave
the generators running against somebody's system.

**Fixed:** the pool is pinned, and every runtime call resolves through what
the run pinned rather than what the pool says now.

**Still open:** `infrastructure/terraform` is empty, and no full load test has
been run end to end on a cluster — that needs the generator image on the
nodes. The launcher is verified; the journey through it is not yet.

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

**Fixed: OIDC single sign-on.** Authorisation code with PKCE, per
organisation. Each check an identity token goes through was verified to be
load-bearing by removing it and watching the right test fail: signature,
issuer, audience, nonce, expiry, and `email_verified`. Group membership maps
to roles in both directions, and the last-administrator guard still holds so a
group edit at the provider cannot leave an organisation unadministrable.
Deactivating a user stops them signing in this way too.

A provider fixture ships with the stack, because single sign-on cannot be
demonstrated or tested without one and pointing `make dev` at somebody else's
service would put it on the critical path of a local start-up. It signs real
RS256 tokens with a real key, and can be asked for the tokens that should be
refused — which is the reason to write one rather than use one.

**Still open:** organisations as a product surface — creating one, and roles
beyond `ORG_ADMIN` and `VIEWER`.

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
7. ~~Kubernetes runtime and Helm chart~~ — done
8. ~~User directory: invite, roles, offboarding~~ — done
9. ~~Backup and restore; `make e2e`; the web interface's missing half~~ — done
10. ~~Credential key rotation~~ — done
11. ~~Pin the generator pool in the run snapshot~~ — done
12. ~~OIDC single sign-on~~ — done
13. Organisations as a product surface; roles beyond two
14. A load test end to end on Kubernetes; Terraform

Each is landed the way the rest of this repository was: a failing test first,
the change, then the gates.
