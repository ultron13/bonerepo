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

**One bug in it shipped and was found later.** The chart's readiness and
liveness probes pointed at `/api/v1/health/ready` and `/api/v1/health/live`;
the API serves `/readyz` and `/healthz`. On a real cluster the readiness probe
would never have succeeded, so the Service would have had no endpoints and the
deployment would never have served anything, while the liveness probe
restarted the pods into a crash loop.

The chart had been "verified against a live cluster" and that claim was too
broad: `--dry-run=server` and an install whose images cannot be pulled
validate the shape of a manifest and never run a probe. Structure was checked;
behaviour was not, and a probe is behaviour. A test now reads the probe paths
out of the chart and makes the request, and was confirmed to fail on the paths
that shipped.

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

The browser side landed a beat later, and finding out why is the point of
having a browser test at all. The first version redirected to a page that did
not exist, so every API check passed and nobody could sign in. Fixing that
surfaced two more: `PUT` was missing from the CORS allow-list, so configuring
a provider failed with "Failed to fetch" — no page had ever used `PUT` — and
the settings page kept showing single sign-on as configured after it was
turned off, because a query keeps its last successful value when the next
fetch errors.

The redirect no longer carries a token at all. The callback sets the refresh
cookie and the landing page trades it for an access token, so nothing valid
sits in browser history.

**Roles: partly closed.** Two roles is a switch, not least privilege. The
distinction an organisation needs is between changing what runs, running it,
and changing who has access — and the last must not come free with either of
the others. `TESTER` and `PERFORMANCE_ENGINEER` sit between the two that
existed: a pipeline that can edit a threshold can make a failing run pass,
which is the one thing a gate must not be able to do.

The routes already asked for permissions rather than roles, so this was a
question of what each role holds rather than a change to any endpoint — which
is also why the map is asserted directly, in a unit test and again over HTTP.
A permission map is only true if the routes read it, and no unit test can tell
whether they do.

**Organisations: closed.** A second tenant needed a hand-written `INSERT`,
which made a multi-tenant product single-tenant in practice — the same shape
as the missing user directory making it single-user. `POST
/api/v1/organizations` creates one together with its first administrator,
because an organisation nobody can enter is only reachable by making somebody,
and making somebody is the same privilege.

It takes an operator's token rather than a role. A `SUPER_ADMIN` able to reach
across organisations would need a path around row-level security, and that is
the tenant boundary; a token that can create an empty organisation cannot read
anybody's data. Unset means refused, verified by unsetting it and watching a
correct-looking value be turned away with nothing created.

**Still open:** `PROJECT_ADMIN` from the data model, which needs a project to
be scoped to and no route resolves one yet.

### 7b. Refresh tokens were unreachable from the browser — closed

Rotation, family revocation and theft detection were all implemented and
tested, and the client could not use any of it. CORS ran with
`allow_credentials=False` and the client never asked for credentials, so the
httpOnly refresh cookie was set at sign-in and never sent back. A session
simply ended when its access token did, fifteen minutes later, and the
carefully built rotation underneath was unreachable.

**Fixed:** credentials are allowed — safe only because the origins are listed
rather than opened, and the cookie is `SameSite=lax` so it does not ride a
cross-site request. Found while wiring single sign-on, which needs the same
cookie.

### 7c. Concurrency was only ever tested one call at a time — closed

Idempotency was tested by repeating a call, which is not the question a CI
fleet asks. Several pipelines start runs in the same second, a person
double-clicks, and a retry lands beside the request it was retrying.
Sequential idempotency and concurrent idempotency are different claims.

**Verified, and it holds.** Eight simultaneous starts get eight distinct run
numbers; simultaneous stops all answer 200; a stop racing a cancel leaves one
ending; a duplicate invite gives one `201` and conflicts rather than a `500`.
The tests use a barrier rather than a thread pool alone — handing work to a
pool dispatches it in quick succession, which is not together, and the
contention would never have happened.

The run counter was already right, and proving the test could tell replaced it
with `max(run_number) + 1` for one run: three of eight starts survived. That
also left the counter behind the table it counts, which broke every start
until it was repaired — so there is now a test asserting no counter has fallen
behind its runs, because that failure arrives long after whatever caused it.

### 7d. Nothing left this system — closed

`webhook_subscriptions` had a table and an ORM model and nothing else: no
repository, no route, no delivery. An earlier sweep for unwired tables counted
the model as a reference, which was too generous. The roadmap wants audit-log
streaming into the SIEMs enterprises already run, and this is the vehicle.

**Fixed:** subscriptions, HMAC-signed deliveries with the timestamp inside the
signature, retries with backoff, and suspension for an endpoint that cannot be
reached. Delivery runs in the worker on its own task, because somebody else's
server answering at somebody else's pace has no business on the path of a
request that already succeeded.

The interesting part is where a webhook may point. A tenant supplies the URL
and the control plane fetches it, which is server-side request forgery in its
purest form. Refusing the obvious literals is not enough — a name resolves, and
it resolves after it is checked — so the host is resolved, every address is
checked, and the delivery connects to a checked address with the hostname
carried in the Host header and in TLS.

Two things were caught by checking rather than by reasoning. A test asserting
that one private address among public ones is refused passed even with only
the first address being checked, because the private one happened to sort
first; it tests both orderings now. And `audit.*` matched nothing at all,
because audit actions are named for what they did — `user.deactivated` — so the
family computed was `user.*`. Nothing raised and nothing logged: the
subscription simply never fired. Every unit and integration test passed. A
smoke test against the running stack found it in one attempt.

### 7e. Three advertised events were published by nothing — closed

Shipped in the same commit whose message explains that a subscription which
never fires is the failure mode worth guarding against. `run.completed`,
`run.failed` and `run.sla_breached` were accepted by the API, stored, listed,
and produced by no code at all. Only `audit.*` was wired.

There is nothing to raise when an event is simply never produced, and no
amount of testing the delivery path finds it. **Fixed:** the worker announces
a run when it ends, and a breach separately, because a breach is what a
pipeline gates on and a completion is not.

The general case is now asserted rather than the three instances: a test reads
the names the contract advertises and checks each against what the code
publishes. It was verified to bite by adding a name nothing produces. Its own
premise is asserted too — it looks for absence, and a path that reads nothing
would find every name absent, or, once the names matched, nothing at all.

### 7f. Every Redis stream grew without bound — closed

`xadd` was called with no `maxlen`, so nothing ever trimmed. Acknowledging a
message does not remove it: Redis keeps what it was given until something says
otherwise, so the memory only goes one way and the end of that is an eviction
or an out-of-memory that takes the control plane down with it.

Found at **96,828 entries** on `metrics.ingestion` on a development machine
that has done nothing but run this test suite.

**Fixed:** an approximate cap per stream, sized by how fast each fills rather
than by how much each matters. Approximate because exact trimming walks the
stream on every add and metrics is the hot path. The cap is a backstop for a
consumer that has stalled, not the normal path — and a consumer far enough
behind to be trimmed has lost a window of metrics, which the ingestion path
already treats as degraded reporting rather than a failed run.

### 7g. The database grew without bound too — closed

Having found the Redis streams uncapped, the same question of Postgres found
the same answer. `performance_metrics` is a hypertable with no retention
policy: an hour-long test at five hundred virtual users writes a row per
transaction per generator per window, and nothing ever removed one. And
nothing cleared finished sign-in sessions — 5,231 refresh families and 5,256
history rows on a development machine two days old.

**Fixed:** a ninety-day retention policy on the metrics hypertable, and an
hourly pass in the worker that clears dead sessions. Verified by ageing three
revoked families and watching the worker clear exactly those three.

Compression was the obvious companion to retention and is **not** enabled:
TimescaleDB refuses it on a table with row-level security. That is a real cost
— roughly an order of magnitude of storage — paid for the tenant boundary, and
it is written down so the next person meets the trade rather than the error.

The purge crosses organisations, which forced RLS refuses, so it goes through
a `SECURITY DEFINER` function that deletes dead families and nothing else. The
alternative was a superuser credential living in the worker for the sake of
one `DELETE`.

Object storage was the third and largest: **496 MB** of raw JTL on the same
development machine, and a real hour-long test writes hundreds of megabytes
per generator. Artifacts now expire by a lifecycle rule on the bucket rather
than by a job — a job would have to list every object to find the old ones,
getting slower exactly as it became more necessary.

That one nearly shipped broken twice over. MinIO refuses
`PutBucketLifecycleConfiguration` without a `Content-Md5` header, which modern
botocore no longer sends, so the rule was rejected and the refusal went only to
a log line. And the test skipped on any `ClientError` — including "no
configuration exists", the exact failure it was written for — so it reported
success while nothing worked. It now skips only where a store cannot do this
at all, and was verified to fail with the call removed.

Audit logs are deliberately left alone. Deleting a compliance record by default
is a worse failure than the disk it costs.

### 7h. The worker did not outlive its broker — closed

Found by stopping Redis to see what the system did. The reads degraded well —
a plain read still served, and starting a run failed in under a second with
"nothing was started" rather than hanging. The worker exited(1) and stayed
exited after Redis came back.

A connection error escaped every loop and left `main`, so a broker restart, a
failover, or a few seconds of network took down the component that
orchestrates every run: while it is gone, nothing starts, nothing is reaped
and nothing is judged. Kubernetes would have restarted the pod and Compose did
not, but neither is the point — a control plane should survive its broker
blinking without needing to be restarted, because the restart is what turns a
blip into a gap.

**Fixed:** every loop is wrapped so a broker failure is a pause with a short
flat backoff. Verified by stopping Redis for twenty seconds: the worker stayed
up, retried nineteen times, recovered when Redis returned, and then
provisioned and started a real run. Graceful shutdown still exits 0 with its
handover message.

The compose services also restart now, which is a smaller point and a real
one: a development stack that stays dead after a dependency blinks teaches the
wrong thing about the system.

### 7i. A dependency being away answered 500 — closed

Completing the sweep that found the worker exiting on a broker outage. The
object store and the database both behaved well when stopped: readiness
reported `not-ready` naming the failing check, liveness stayed 200 in three
milliseconds without touching anything, reads failed in milliseconds rather
than hanging, a run failed cleanly with zero generators, and both processes
survived and recovered.

What was wrong was the answer. A stopped database produced **500**, which
tells a client the server has a bug: a CI pipeline will not retry it, a load
balancer will not take the instance out, and the page goes to whoever owns the
code rather than whoever owns the database. It is now **503** with
`Retry-After`.

The first fix caught SQLAlchemy's `OperationalError` and never fired. A
container that is stopped stops resolving, so the failure arrives as
`socket.gaierror` before any driver is involved -- which is also the ordinary
shape of an outage behind a Kubernetes Service with no endpoints. The handlers
are named types rather than a broad `OSError`, so a genuine bug is still 500
rather than hidden behind a retry loop, and `IntegrityError` stays 500 too
because a row that breaks a constraint will break it identically for ever.

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
