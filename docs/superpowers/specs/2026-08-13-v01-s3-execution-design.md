# Design — v0.1 slice 3: Execution

**Date:** 2026-08-13 · **Status:** Approved · **Slice:** 3 of 5

The third implementable slice of [v0.1](../../roadmap.md). Its goal is that a
test defined in S2 becomes running JMeter processes on real containers, and
that the run stops cleanly and leaves its raw artifacts in object storage.

S2 ends with the platform able to say whether a test will run. S3 makes it run.
Nothing here interprets a measurement: the JTL is uploaded, not parsed. That is
S4's work, and keeping the line there is what makes this slice demonstrable on
its own — a run happened, here is what it produced.

## Scope of S3

**In:** the `MessageBus` interface and its Redis Streams implementation · the
worker process · the run state machine · run creation with an immutable
`configuration_snapshot` · generator allocation · the plan bundle · the
`GeneratorRuntime` interface and `DockerRuntime` · the generator image ·
`plimsoll-agent` · the agent WebSocket protocol and run-scoped tokens · the
`Executor` interface and `JMeterExecutor` · artifact upload and download ·
`stop` and `cancel` · capacity-loss handling · the run API ·
`POST /generator-pools/{id}/test-connection`.

**Out:** everything that reads a sample. JTL tailing, HDR folding, the merge
worker, the metrics hypertable, live metric streaming, grouped errors in
`run_errors`, and SLA evaluation are S4. `sla_result` stays null and `summary`
carries only what the orchestrator itself knows. `KubernetesRuntime` is out: the
`GeneratorRuntime` interface exists with Docker as its one implementation, the
same way [ADR-0006](../../adr/0006-jmeter-first-executor.md) validates the
executor seam with a single real executor.
[ADR-0003](../../adr/0003-kubernetes-native-generators.md) governs the
Kubernetes implementation when it arrives with the Helm chart. The web
application is S5.

**No migration.** `test_runs`, `run_generators`, `run_errors`, and
`project_run_counters` all landed in the S1 schema. As in S2, if that turns out
to be false the gap is a bug in the S1 schema and is fixed as one.

## How S3 is split

One design, two implementation plans. The split is orchestration first, load
second, because the orchestration carries every invariant this slice can
violate and is worth testing without a JMeter container in the loop.

| Plan | Delivers |
| --- | --- |
| **S3a Orchestration** | Message bus, worker, run creation and state machine, allocation, `DockerRuntime`, the agent channel and run-scoped tokens, the run API, `test-connection`. The agent registers, heartbeats, and exits — it does not yet run anything |
| **S3b Load and artifacts** | JMeter in the generator image, the `Executor` seam, the plan bundle, in-memory secret injection, the agent's target re-check, artifact upload and download |

The S3a agent is not a throwaway stub. Register, heartbeat, state reporting and
the terminal handshake are the agent's spine; S3b adds one step in the middle
of a state machine that already works.

## Processes and packaging

Four processes across three trust zones.

| Process | Package | Runs | Trusted with |
| --- | --- | --- | --- |
| API | `plimsoll_api` | Existing container | Everything: database, keys, object store |
| Worker | `plimsoll_worker` | **The API's image**, different command | The same, minus inbound HTTP |
| Agent | `plimsoll_agent` | Inside every generator | Its run, for the length of its run |
| Generator | `images/generator` | Ephemeral, per run | Nothing durable |

The worker ships in the API image because it is the same codebase
([ADR-0001](../../adr/0001-modular-monolith.md)) and because `make dev` should
gain a container, not a second image build. It is a separate *service* with its
own command, so it scales and fails independently — which is the boundary the
ADR actually cares about.

The agent is the opposite: it must **not** depend on `plimsoll-api`. It ships
inside a container that executes user-supplied plans, so it carries no database
driver, no key provider, and no object-store client. Its dependencies are
`plimsoll-contracts` for the protocol models, `packages/executor-sdk`, and an
HTTP/WebSocket client. Its artifact uploads are plain `PUT`s to URLs somebody
else signed.

Two new workspace members, `apps/worker` and `apps/agent`, plus
`packages/executor-sdk`, join the `uv` workspace.

Where the run domain lives: in `plimsoll_api`, with every other domain —
`repositories/runs.py`, `services/runs.py`, `routers/runs.py`. The worker calls
those services rather than writing its own SQL, which is ADR-0001's rule about
cross-module calls going through service interfaces rather than reaching into
another module's tables.

## The message bus

`plimsoll_api/messaging.py` holds the seam
[ADR-0005](../../adr/0005-redis-streams-over-rabbitmq.md) promises:

```python
class MessageBus(Protocol):
    async def publish(self, stream: str, message: dict[str, str]) -> None: ...
    async def consume(
        self, stream: str, group: str, consumer: str
    ) -> AsyncIterator[Delivery]: ...
    async def acknowledge(self, delivery: Delivery) -> None: ...
    async def reclaim_stale(self, stream: str, group: str, idle: timedelta) -> list[Delivery]: ...
```

`RedisStreamBus` implements it with consumer groups. `reclaim_stale` is what
makes a dead worker recoverable: its pending message is claimed by another
consumer after an idle timeout.

Delivery is **at-least-once**, which is a design constraint rather than a
footnote — see the reconciler below.

One stream in this slice: `runs.execution`, carrying `{runId, organizationId}`
and nothing else. The run row is the payload; a message that duplicated state
would be a second source of truth. The organisation is addressing rather than
state: every query is organisation-scoped under RLS
([invariant 4](../../../CLAUDE.md)), so a consumer cannot read the run at all
until it knows which tenant to bind its session to.

## Starting a run

`POST /api/v1/tests/{testId}/runs` requires the new `TEST_EXECUTE` permission.
Starting load is not the same right as editing a test, and a `VIEWER` holds
neither.

The endpoint runs **S2's preflight first**, outside any transaction because it
talks to Git, and refuses a failing test with `422 TEST_NOT_RUNNABLE` carrying
every failing check in `details`. This is [invariant 8](../../../CLAUDE.md) and
the same rule S2 established: report the whole set, not the first problem.

On a pass, one transaction:

1. Take the project's `project_run_counters` row with `SELECT … FOR UPDATE` and
   allocate `run_number`. Two concurrent starts serialise on that row instead of
   racing `UNIQUE (project_id, run_number)`, and a rolled-back creation gives
   its number back.
2. Insert `test_runs` at status `QUEUED`, `trigger_source = 'API'`.
3. Write `configuration_snapshot` **from the commit SHAs preflight just
   resolved**, so the thing that was checked and the thing that will execute
   cannot disagree. The snapshot carries plans with their SHAs, the workload,
   the generator allocation, the SLA rules, and the target policy version and
   allowlist — [invariant 3](../../../CLAUDE.md).
4. Record the audit row.

After the transaction commits, publish to `runs.execution`. Publishing inside the
transaction would hold it open across a network call, which S2's constraint
forbids, and would let a consumer read the run before it exists.

If the publish itself fails, the request marks the run `FAILED` with a reason
and says so: the caller learns synchronously rather than watching a run sit at
`QUEUED`.

**A known gap, recorded rather than papered over.** A process that dies in the
window between commit and publish leaves a `QUEUED` run that no consumer knows
about. Recovering it automatically means finding stuck runs across every tenant,
and [invariant 4](../../../CLAUDE.md) means no ordinary connection can read
across tenants. The fix is the shape S1 already used for the login lookup — a
`SECURITY DEFINER` function owned by a `NOLOGIN BYPASSRLS` role, returning only
`(run_id, organization_id)` and nothing else, the way `auth_lookup_user` returns
only the login columns. It is deferred because it is a schema change and this
slice adds none, and because the window is a crash between two adjacent
statements. The run stays visible at `QUEUED` throughout; it is stalled, not
lost.

Nothing about run creation executes load. The API enqueues; the worker
orchestrates; generators execute — [invariant 1](../../../CLAUDE.md).

## The reconciler

**The worker is a reconciler, not a script.** On each tick — a delivery, a
timer, or a sweep — it loads the run and its generators, compares desired state
to actual, and takes the next action. Three properties follow, and all three are
required rather than nice:

- **Duplicate deliveries are harmless.** At-least-once means the same `runId`
  can arrive twice; a linear script would provision twice and double the load.
  Provisioning is guarded by `run_generators` rows: a row that already carries an
  `external_ref` is not provisioned again.
- **A dead worker resumes.** Nothing important lives in worker memory, so a
  reclaimed message rebuilds its picture from the database.
- **Transitions cannot race.** Every status change is a conditional
  `UPDATE … WHERE status = :expected`, the same optimistic guard S2 uses on the
  test document. A transition that loses the race did not happen.

## Generator allocation

Generators are `ceil(total_users / max_vus_per_generator)`, capped by the pool's
`max_generators`. If the cap cannot cover the request the run fails immediately
with a capacity reason — preflight already checks this, but a pool can shrink
between validating and starting, and the orchestrator does not trust a check it
did not just perform.

Users are split evenly, with the remainder distributed one at a time to the
earliest ordinals, so the total reconciles exactly. `run_generators` rows are
written with `ordinal`, `assigned_users`, and status `PENDING`, and the
allocation is copied into `configuration_snapshot`.

## The plan bundle

The worker fetches the plan **once**, at the pinned SHA, with the Git client S2
already built, then packs the plan's directory — the `.jmx`, its data files,
and `plimsoll.yaml` — into `runs/{runId}/bundle.tar.gz` and records its
SHA-256. Agents download it by presigned `GET` and verify the digest.

This deviates from [the execution plane](../../architecture/02-execution-plane.md),
which has each agent clone at the pinned SHA. The reason is credentials: a clone
per generator means handing the customer's Git credential to every container
running a user-supplied plan, and putting `git` and credential handling inside
the generator image. Staging centrally keeps the credential in the control
plane, removes `git` from the image, replaces N fetches with one, and makes
"every generator ran byte-identical inputs" a checksum rather than an
assumption. The architecture document is corrected in this slice.

## Provisioning

```python
class GeneratorRuntime(Protocol):
    async def provision(self, plan: ExecutionPlan) -> list[GeneratorHandle]: ...
    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorStatus]: ...
    async def teardown(self, handles: list[GeneratorHandle]) -> None: ...
```

`DockerRuntime` talks to the Docker daemon over its socket, mounted into the
worker container. That is a real privilege — the socket is root-equivalent on
the host — and it is the only way a compose stack creates sibling containers
without running Docker-in-Docker, which is heavier and privileged too. It is
scoped to the worker; the API never gets it.

Each container is created on the compose network with `restart: no`
([invariant 6](../../../CLAUDE.md) — a restarted generator resets virtual-user
state mid-test and silently corrupts the run), a memory and CPU limit from the
pool config, and an environment carrying the API URL, the run id, the ordinal,
the assigned users, and the run-scoped token. `external_ref` records the
container id, which is what makes provisioning idempotent and teardown possible
after a worker restart.

Teardown runs in a `finally` and is idempotent: a container already gone is a
success, not an error.

## Run-scoped tokens

A JWT signed with the existing key, `aud: "agent"`, claiming the run id, the
ordinal, and the organisation, expiring at start plus duration plus a grace
margin. It authorises exactly two things: the agent channel for its own run, and
artifact URLs for its own ordinal. It is the whole reason there is no
long-lived registration secret sitting on a generator
([security](../../architecture/05-security.md)) — generators do not outlive
their run, so neither does their credential.

## The agent protocol

Outbound-only WebSocket to the API at `/api/v1/agent/runs/{runId}`, which is
what a generator in a locked-down network can do. Messages are Pydantic models
in `plimsoll-contracts`, so both ends share one definition.

Agent to control plane: `register` (ordinal, agent version), `state`
(`FETCHING`, `READY`, `RUNNING`, `STOPPING`, `COMPLETED`, `FAILED`, with a
reason), `heartbeat` (every 10 seconds), `artifact_url_request` (a name).

Control plane to agent: `command` (`start`, `stop`, `cancel`),
`artifact_url` (a presigned `PUT`), and the heartbeat acknowledgement, which
**carries the current desired state**.

Desired state lives in Postgres. A change is announced on a Redis pub/sub
channel, and whichever API process holds that agent's socket pushes the command.
The heartbeat acknowledgement repeats the desired state so a missed push
self-heals within one interval instead of hanging a run. The worker never talks
to an agent directly; it changes state through the run service, exactly as the
API does.

**Agents wait to be told to start.** Every agent reaches `READY` and stops
there; the worker commands `start` only when all of them have. Staggered starts
smear the ramp across generators and quietly distort the result, which is the
kind of error that never announces itself.

Agent lifecycle: `STARTING → FETCHING → READY → RUNNING → STOPPING →
COMPLETED`, with `FAILED` reachable from any of them.

## Running the plan

`packages/executor-sdk` holds the seam:

```python
class Executor(Protocol):
    def command(self, context: ExecutionContext) -> list[str]: ...
    def artifacts(self, context: ExecutionContext) -> list[Path]: ...
    def interpret(self, exit_code: int) -> ExecutionOutcome: ...
```

`JMeterExecutor` is the one implementation, and the invocation is the one
ADR-0006 fixes:

```
jmeter -n -t <plan>.jmx -l results.jtl -Jthreads=<n> -Jrampup=<s> -Jduration=<s>
```

Workload parameters are properties. **The plan is never rewritten** — a plan
that runs here and in the tester's local JMeter is the same file, and rewriting
it would make every result unreproducible outside the platform.

Before the first request, the agent resolves the variables it needs from the
values the control plane sent over the channel, injecting them as properties in
memory, never to disk. It then re-checks every target host in the plan against
the allowlist in the snapshot and refuses to start if one fails. That second
check exists because the first happened minutes earlier against configuration
that could have changed, and because a run is where the traffic actually leaves
the machine.

A non-zero exit means JMeter itself failed — a broken plan, a missing plugin, a
bad property — and fails that generator. Sampler errors inside a clean run are
results, not failures, and S3 does not read them.

## Artifacts

The agent asks for a presigned `PUT` per artifact and uploads `results.jtl` and
`jmeter.log` to `runs/{runId}/generators/{ordinal}/`. It never holds an
object-store credential and never learns the bucket layout — the control plane
signs the exact key it is willing to accept.

`GET /api/v1/runs/{runId}/artifacts` lists what landed;
`GET /api/v1/runs/{runId}/artifacts/{name}` answers `302` to a presigned `GET`,
so a download is one `curl -L` rather than a two-step negotiation. The bucket is
created idempotently at worker startup, so `make dev` needs no extra step and a
deployment needs no manual one.

An upload that fails is retried with backoff and then recorded as a warning in
`summary`. A completed run with a missing log is worse than one with it, and far
better than pretending the run did not happen.

## Stopping, cancelling, and finishing

`POST /runs/{id}/stop` winds down and keeps results; `POST /runs/{id}/cancel`
abandons the run. Both are a state write plus an announcement, which is what
makes them idempotent by construction: repeating either on an already-stopped
run returns `200`, announces nothing new, and re-runs no side effect —
[invariant 5](../../../CLAUDE.md). Stopping before `RUNNING` tears down and ends
the run `CANCELLED`: there is no load to wind down and no result to keep, and
calling that outcome `COMPLETED` would put a run with no data beside runs that
have some.

A run finishes when every generator is terminal: the worker tears the containers
down, writes `summary`, and sets `COMPLETED`. `sla_result` stays null until S4
has numbers to judge.

Run states in this slice: `QUEUED → ALLOCATING → STARTING → RUNNING →
STOPPING → COMPLETED`, with `FAILED` from any active state and `CANCELLED` from
a cancel. The state machine in
[the execution plane](../../architecture/02-execution-plane.md) opens with
`DRAFT`, `READY`, and `SCHEDULED`, which are states of a *test*, not of a run;
the diagram is corrected in this slice.

## Failure handling

| Failure | Result |
| --- | --- |
| Three missed heartbeats | Generator marked `LOST`; capacity loss recomputed |
| Capacity loss below threshold | Run continues, `degraded` set, warning recorded |
| Capacity loss at or above threshold | Run fails; survivors stopped and torn down |
| Provisioning error | Run fails with a structured reason; everything created is torn down |
| Agent never reaches `READY` in time | Run fails from `STARTING` |
| JMeter exits non-zero | That generator fails, and counts toward capacity loss |
| Target re-check fails at the agent | Agent refuses to generate traffic; run fails |
| Artifact upload fails after retries | Warning in `summary`; run still completes |
| Worker dies mid-run | Message reclaimed after the idle timeout; state rebuilt from the database |

Capacity loss is lost assigned users over planned total. The threshold defaults
to 10% and becomes an optional `maxCapacityLossPercent` on the workload
contract — a jsonb field, so still no migration. A degraded run is marked as
such because a result produced with 82% of the intended load must never be
silently compared against one produced with 100%.

## Endpoints

```http
POST   /api/v1/tests/{testId}/runs        # TEST_EXECUTE — 201, or 422 with every failing check
GET    /api/v1/projects/{projectId}/runs  # TEST_READ
GET    /api/v1/runs/{runId}               # TEST_READ
GET    /api/v1/runs/{runId}/status        # TEST_READ — the cheap poll
POST   /api/v1/runs/{runId}/stop          # TEST_EXECUTE — idempotent
POST   /api/v1/runs/{runId}/cancel        # TEST_EXECUTE — idempotent
GET    /api/v1/runs/{runId}/artifacts     # TEST_READ
GET    /api/v1/runs/{runId}/artifacts/{name}
POST   /api/v1/generator-pools/{id}/test-connection   # ADMIN_SYSTEM

WS     /api/v1/agent/runs/{runId}         # run-scoped token, aud: agent
```

`Idempotency-Key` on run creation is v0.2. The header is not honoured and not
advertised in this slice, because a header that looks honoured and is not is
worse than an absent one.

## Testing

**Unit** — the parts that are pure, and they are chosen so a wrong answer is
visible: allocation (remainder reconciles exactly, caps respected, an
impossible request refused), the reconciler's decision function given a run and
its generators, JMeter command construction, capacity-loss arithmetic, the
protocol models.

**Integration**, against `make dev` and a real Docker daemon:

- A run against `demo-target` across two generators reaches `COMPLETED`, and
  two `results.jtl` objects exist under the run's prefix.
- `configuration_snapshot` holds the resolved commit SHA, and it is the SHA the
  agents were given.
- `stop` twice returns `200` twice, tears down once, and writes one audit row.
- A test that fails preflight returns `422` listing every failing check, and
  creates no run row.
- A `VIEWER` is refused run creation, stop, and cancel — automatically, by the
  existing sweep that enumerates every write route from the OpenAPI document.

**Two tests exist to fail if an invariant is removed**, in the spirit of S1's
tenancy test:

- `docker kill` on one generator mid-run produces a degraded or failed run —
  never a quietly short one that reports success.
- Publishing the same execution message twice yields N containers, not 2N.

## Acceptance criteria

- [ ] A test defined in S2 starts a run over HTTP and reaches `COMPLETED`
- [ ] JMeter really runs: the JTL contains samples against `demo-target`
- [ ] Raw artifacts for every generator land in object storage and download
      through the API
- [ ] `configuration_snapshot` pins the commit SHAs that executed
- [ ] Generators are created with no restart policy, and are gone after the run
- [ ] `stop` and `cancel` are idempotent, and stopping a stopped run is `200`
- [ ] Losing a generator mid-run marks the run degraded, or fails it above the
      threshold
- [ ] A run cannot start unless preflight passes, and a failure lists every
      failing check
- [ ] No Git credential and no object-store credential reaches a generator
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`,
      and `make contracts` all pass, the last leaving the tree clean

## Decisions taken in this design

Recorded because they were not in the specification before now.

1. **The control plane stages the plan bundle; agents do not clone.** The
   deciding factor is that a clone per generator puts the customer's Git
   credential inside every container running a user-supplied plan.
   [The execution plane](../../architecture/02-execution-plane.md) is corrected
   in this slice.
2. **The worker ships in the API's image as a second service.** One codebase,
   one build, independent scaling and failure. The Docker socket is mounted only
   into the worker.
3. **The agent does not depend on `plimsoll-api`.** Different trust zone; it
   gets protocol models and an executor, and nothing that talks to a database or
   a key.
4. **Desired state lives in Postgres and is announced over Redis pub/sub**,
   with the heartbeat acknowledgement repeating it. There is no command queue to
   an agent, because a queue would let a stale command arrive after the state it
   was based on had changed.
5. **Agents wait for a coordinated start.** A ramp smeared across generators is
   a wrong result that looks like a right one.
6. **The initial run state is `QUEUED`**, as the API document already says. The
   execution plane's diagram opens with test states and is corrected.
7. **`TEST_EXECUTE` joins the permission catalogue.** Starting load is not the
   same right as editing a test.
8. **Delivery is at-least-once and the worker is a reconciler**, rather than
   pursuing exactly-once. Exactly-once across a broker and a container runtime
   is not achievable; idempotent actions guarded by persisted state are.
9. **No `Idempotency-Key` on run creation.** It is v0.2, and a header that looks
   honoured but is not is worse than an absent one.
10. **The execution message carries the organisation id**, and the recovery
    sweep for a run stalled by a crash between commit and publish is deferred.
    Both follow from RLS: a consumer cannot read a run without knowing its
    tenant, and cannot find stuck runs across tenants without the scoped
    `SECURITY DEFINER` lookup S1 used for login — which is a migration, and this
    slice adds none.

None of these contradicts an ADR. Item 1 contradicts an architecture document
and is the one worth disagreeing with now; item 4 shapes the protocol S4 extends.
