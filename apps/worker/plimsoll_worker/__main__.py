"""The worker process: consume, reconcile, repeat.

It ships in the API's image with a different command. One codebase (ADR-0001),
one build, and a service that scales and fails on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.logging import configure_logging
from plimsoll_api.messaging import (
    ERRORS_KIND,
    METRICS_GROUP,
    METRICS_INGESTION,
    POOL_PROBES,
    RUNS_EXECUTION,
    WEBHOOK_DELIVERIES,
    WEBHOOK_GROUP,
    WORKER_GROUP,
    Delivery,
    RedisStreamBus,
    get_bus,
    live_channel,
    probe_channel,
    run_channel,
)
from plimsoll_api.observability import (
    METRIC_WINDOWS,
    WORKER_DECISIONS,
    WORKER_FAILURES,
    mark_tick,
)
from plimsoll_api.repositories import pools as pools_repo
from plimsoll_api.repositories import run_errors as errors_repo
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import issue_agent_token
from plimsoll_api.services import results as results_service
from plimsoll_api.services import script_repos
from plimsoll_api.services.sla import evaluate
from plimsoll_api.storage import ensure_bucket, presign_get
from plimsoll_contracts.agent import Command
from plimsoll_contracts.metrics import percentile
from plimsoll_contracts.performance_tests import SlaRuleSpec
from plimsoll_contracts.runs import GeneratorStatus, RunStatus
from plimsoll_worker.bundle import BundleRef, stage
from plimsoll_worker.events import (
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_SLA_BREACHED,
    announce_run,
)
from plimsoll_worker.maintenance import purge_dead_sessions
from plimsoll_worker.metrics import merge_batch, write
from plimsoll_worker.reconciler import Decision, GeneratorRow, RunView, decide, is_silent
from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime
from plimsoll_worker.runtime.kubernetes import KubernetesRuntime
from plimsoll_worker.serving import serve
from plimsoll_worker.webhooks import deliver as deliver_event

TICK_SECONDS = 2
# The probe consumer's own block. The API waits five seconds for an answer, so
# this stays well inside that and costs one idle connection between probes.
PROBE_BLOCK_MS = 2000
# Metrics arrive continuously during a run. A batch is merged before it is
# written, so a wider read is fewer round trips and a better merge.
METRICS_BLOCK_MS = 2000
METRICS_BATCH = 500
# Kubernetes sends SIGTERM and then kills, by default thirty seconds later. The
# loops finish the tick they are in and stop; anything longer than this and the
# kill arrives first, which is the case adoption exists to survive.
SHUTDOWN_GRACE_SECONDS = 20
# Hourly. Nothing here is urgent, and a loop that runs often costs a query.
MAINTENANCE_INTERVAL_SECONDS = 3600
# Where a scraper and a liveness probe find the worker. It serves nothing else.
SERVE_PORT = int(os.environ.get("PLIMSOLL_WORKER_PORT", "9100"))
# How much longer than the run an agent's credential stays valid. It covers
# provisioning, the ramp, and the wind-down; it is not a licence to outlive the
# run by any margin that matters.
GRACE_SECONDS = 120
RECLAIM_AFTER = timedelta(seconds=60)
# The last window a generator sends is already on the ingestion stream by the
# time it reports COMPLETED, but the metrics loop may not have consumed it. A
# verdict computed a moment too early would judge the run on all but its final
# seconds, so completion waits for the stream to catch up.
METRICS_SETTLE_SECONDS = 6
NETWORK = os.environ.get("PLIMSOLL_GENERATOR_NETWORK", "plimsoll_default")
INTERNAL_API_URL = os.environ.get("PLIMSOLL_INTERNAL_API_URL", "http://api:8000")

logger = logging.getLogger("plimsoll.worker")

# The statuses a run may still be moved out of. A run that has already ended is
# not walked backwards by a late tick.
LIVE_RUN_STATUSES: list[str] = [
    RunStatus.QUEUED,
    RunStatus.ALLOCATING,
    RunStatus.STARTING,
    RunStatus.RUNNING,
    RunStatus.STOPPING,
]


# A pool declares which one it uses. Both create the same generator image
# from the same spec; only the launcher differs, which is what keeps `make dev`
# honest about production.
RUNTIMES: dict[str, Callable[[], Any]] = {
    "docker": DockerRuntime,
    "kubernetes": KubernetesRuntime,
}


class Orchestrator:
    def __init__(self) -> None:
        self._runtimes: dict[str, Any] = {}
        self._bus = get_bus()

    def runtime(self, name: str) -> Any:
        """Built once each and kept: a client carries a connection pool, and
        one per run would leak sockets for as long as the worker lives."""
        if name not in RUNTIMES:
            raise RuntimeError(f"No runtime is implemented for {name}.")
        if name not in self._runtimes:
            self._runtimes[name] = RUNTIMES[name]()
        return self._runtimes[name]

    async def _runtime_for(self, run: sa.Row[Any], org_id: uuid.UUID) -> Any:
        """The runtime the run pinned, not the one its pool names now.

        A pool switched from docker to kubernetes mid-run would otherwise
        strand the generators: teardown would look in the new runtime, find
        nothing, and leave the old ones running against somebody's system.
        """
        pinned = (run.configuration_snapshot or {}).get("pool") or {}
        if pinned.get("runtime"):
            return self.runtime(str(pinned["runtime"]))
        # A run created before the pool was pinned. The live pool is the only
        # thing left to ask, which is exactly the weakness being removed.
        async with session_for_org(org_id) as session:
            pool = await pools_repo.get(
                session, uuid.UUID(run.configuration_snapshot["workload"]["generatorPoolId"])
            )
        return self.runtime(pool.runtime if pool is not None else "docker")

    async def reconcile(self, run_id: uuid.UUID, org_id: uuid.UUID) -> bool:
        """Returns whether the run is finished with. One tick, one decision."""
        async with session_for_org(org_id) as session:
            run = await repo.get(session, run_id)
            if run is None:
                # Its transaction rolled back, or it never committed. Nothing
                # to do, and nothing to keep.
                return True
            generators = await repo.generators_for(session, run_id)

        workload = run.configuration_snapshot["workload"]
        view = RunView(
            status=run.status,
            generators=[
                GeneratorRow(
                    ordinal=row.ordinal,
                    status=row.status,
                    assigned_users=row.assigned_users,
                    external_ref=row.external_ref,
                    last_heartbeat=row.last_heartbeat,
                )
                for row in generators
            ],
            now=datetime.now(UTC),
            duration_seconds=int(workload["durationSeconds"]),
            started_at=run.started_at,
            max_capacity_loss_percent=int(workload.get("maxCapacityLossPercent", 10)),
        )
        decision = decide(view)
        WORKER_DECISIONS.labels(decision=decision.name).inc()
        logger.info(
            "run %s is %s with %d generators: %s",
            run_id,
            run.status,
            len(view.generators),
            decision.name,
        )

        if decision is Decision.DONE:
            # A run can be ended without the worker's help -- a cancel over
            # HTTP does exactly that. Whoever ends it, the containers are this
            # process's to remove, so the last tick reaps before letting go.
            await self._reap(run, org_id)
            return True
        if decision is Decision.PROVISION:
            await self._provision(run, org_id, view)
        elif decision is Decision.START:
            await self._command(run.id, org_id, RunStatus.RUNNING, Command.START)
        elif decision is Decision.MARK_LOST:
            await self._mark_lost(run.id, org_id, view)
        elif decision is Decision.CONTINUE_DEGRADED:
            async with session_for_org(org_id) as session:
                await repo.mark_degraded(session, run.id)
        elif decision is Decision.FAIL:
            await self._finish(run, org_id, RunStatus.FAILED)
            return True
        elif decision is Decision.FINISH:
            await self._finish(run, org_id, RunStatus.COMPLETED)
            return True
        return False

    async def _provision(self, run: sa.Row[Any], org_id: uuid.UUID, view: RunView) -> None:
        async with session_for_org(org_id) as session:
            # ALLOCATING as well as QUEUED: re-entering is how a run
            # abandoned part-way through recovers, and the conditional update
            # still refuses a second worker running concurrently.
            moved = await repo.transition(
                session,
                run.id,
                expected=[RunStatus.QUEUED, RunStatus.ALLOCATING],
                to=RunStatus.ALLOCATING,
            )
        if moved is None:
            # Another worker took it, or it has already moved past allocation.
            # Provisioning twice is exactly the failure at-least-once delivery
            # invites, and this is where it is refused.
            return

        # Before anything is created: a previous attempt may have made
        # containers and died before recording them. Adopting them makes the
        # rows describe what exists, which is what lets them be torn down and
        # what stops this attempt colliding with their deterministic names.
        adopted = await self._adopt_orphans(run, org_id, view)

        image, resources = await self._pool_settings(run, org_id)
        ttl = view.duration_seconds + GRACE_SECONDS

        try:
            # Once per run, before any container exists. An unreachable
            # repository must fail the run, not produce generators with
            # nothing to execute.
            bundle = await self._stage(run, org_id)
        except Exception:
            await self._finish(run, org_id, RunStatus.FAILED)
            raise
        bundle_url = presign_get(bundle.key, seconds=ttl)

        specs = [
            GeneratorSpec(
                run_id=run.id,
                ordinal=generator.ordinal,
                image=image,
                network=NETWORK,
                environment={
                    "PLIMSOLL_API_URL": INTERNAL_API_URL,
                    "PLIMSOLL_RUN_ID": str(run.id),
                    "PLIMSOLL_ORDINAL": str(generator.ordinal),
                    "PLIMSOLL_RUN_TOKEN": issue_agent_token(
                        run.id, ordinal=generator.ordinal, org_id=org_id, ttl_seconds=ttl
                    ),
                    # A URL, not a credential: it grants this one object for
                    # this one run's lifetime and nothing else.
                    "PLIMSOLL_BUNDLE_URL": bundle_url,
                    "PLIMSOLL_BUNDLE_SHA256": bundle.sha256,
                },
                labels={"plimsoll.run": str(run.id)},
                memory_limit=resources.get("memoryLimit"),
                cpu_limit=resources.get("cpuLimit"),
            )
            # An ordinal that already carries a container is never given a
            # second one, so a retried provision fills only the gaps.
            for generator in view.generators
            if generator.external_ref is None and generator.ordinal not in adopted
        ]

        try:
            handles = await (await self._runtime_for(run, org_id)).provision(specs)
        except Exception:
            # provision is all-or-nothing, so there is nothing to reap here --
            # only a run to fail, loudly rather than by stalling in ALLOCATING.
            await self._finish(run, org_id, RunStatus.FAILED)
            raise

        async with session_for_org(org_id) as session:
            for handle in handles:
                await repo.attach_external_ref(session, run.id, handle.ordinal, handle.external_ref)
            await repo.transition(
                session, run.id, expected=[RunStatus.ALLOCATING], to=RunStatus.STARTING
            )

    async def _adopt_orphans(self, run: sa.Row[Any], org_id: uuid.UUID, view: RunView) -> set[int]:
        """Record containers this run already has that no row describes.

        A worker that died between creating a container and writing its
        reference left one the database never heard of: nothing reaps it, and
        the next attempt collides with its name. Graceful shutdown does not
        help here -- a SIGKILL, an OOM, or a lost node all produce the same
        orphan -- so recovery reads the runtime rather than trusting that the
        last write happened.

        Returns the ordinals that were adopted.
        """
        known = {row.ordinal for row in view.generators if row.external_ref is not None}
        run_id = run.id
        existing = await (await self._runtime_for(run, org_id)).find_by_run(run_id)
        orphans = [handle for handle in existing if handle.ordinal not in known]
        if not orphans:
            return set()

        logger.warning(
            "run %s has %d container(s) no row describes; adopting them",
            run_id,
            len(orphans),
        )
        async with session_for_org(org_id) as session:
            for handle in orphans:
                await repo.record_external_ref(session, run_id, handle.ordinal, handle.external_ref)
        return {handle.ordinal for handle in orphans}

    async def _stage(self, run: sa.Row[Any], org_id: uuid.UUID) -> BundleRef:
        """Git access is assembled inside a transaction; the fetch happens
        outside one -- a clone must never hold a database connection open."""
        async with session_for_org(org_id) as session:
            plans = []
            for plan in run.configuration_snapshot["plans"]:
                repo_row = await script_repos.require(session, uuid.UUID(plan["scriptRepoId"]))
                plans.append(
                    {
                        "access": await script_repos.access_for(session, repo_row),
                        "commitSha": plan["commitSha"],
                        "planPath": plan["planPath"],
                    }
                )
        reference = await stage(run.id, plans)
        async with session_for_org(org_id) as session:
            await repo.record_bundle_digest(session, run.id, reference.sha256)
        return reference

    async def _pool_settings(
        self, run: sa.Row[Any], org_id: uuid.UUID
    ) -> tuple[str, dict[str, Any]]:
        """The image and the resources a generator from this pool gets.

        Sizing belongs to the pool because it belongs with the capacity the
        pool already declares: a pool driving 2000 virtual users per generator
        needs a bigger heap and a bigger container than one driving 200, and
        the two numbers have to move together.
        """
        snapshot = run.configuration_snapshot
        pinned = snapshot.get("pool") or {}
        if pinned.get("image"):
            resources = {
                key: pinned[key]
                for key in ("memoryLimit", "cpuLimit")
                if pinned.get(key) is not None
            }
            return str(pinned["image"]), resources

        # A run created before the pool was pinned. Reading the live pool is
        # what this replaced -- a pool edited after the run was accepted used
        # to change what executed -- so it stays only for runs already queued
        # when this landed.
        async with session_for_org(org_id) as session:
            pool = await pools_repo.get(session, uuid.UUID(snapshot["workload"]["generatorPoolId"]))
        if pool is None:
            raise RuntimeError(f"Run {run.id} names a generator pool that no longer exists.")

        config = dict(pool.config)
        resources = {
            key: config[key] for key in ("memoryLimit", "cpuLimit") if config.get(key) is not None
        }
        return str(config["image"]), resources

    async def _command(
        self, run_id: uuid.UUID, org_id: uuid.UUID, status: RunStatus, command: Command
    ) -> None:
        async with session_for_org(org_id) as session:
            moved = await repo.transition(
                session,
                run_id,
                expected=[RunStatus.STARTING],
                to=status,
                started=status is RunStatus.RUNNING,
            )
        if moved is None:
            # A stop arrived first. Announcing START now would contradict what
            # the database already says, and the database is the truth.
            return
        # The announcement is a nudge; the agent's next heartbeat carries the
        # same command, so a lost publish costs one interval rather than the run.
        await self._bus.announce(run_channel(run_id), {"command": command.value})

    async def _mark_lost(self, run_id: uuid.UUID, org_id: uuid.UUID, view: RunView) -> None:
        async with session_for_org(org_id) as session:
            for generator in view.generators:
                if is_silent(generator, view.now):
                    await repo.set_generator_status(
                        session, run_id, generator.ordinal, GeneratorStatus.LOST
                    )

    async def check_image(self, runtime: str, image: str) -> tuple[bool, str]:
        ok, detail = await self.runtime(runtime).check(image)
        return bool(ok), str(detail)

    async def _reap(self, run: sa.Row[Any], org_id: uuid.UUID) -> list[GeneratorHandle]:
        """Remove every container the run created.

        Idempotent, which is what lets it run on every terminal path without
        anyone having to work out whether it already ran: a container that is
        already gone is the outcome this wanted.
        """
        async with session_for_org(org_id) as session:
            rows = await repo.generators_for(session, run.id)
        handles = [
            GeneratorHandle(ordinal=row.ordinal, external_ref=row.external_ref)
            for row in rows
            if row.external_ref
        ]
        await (await self._runtime_for(run, org_id)).teardown(handles)
        return handles

    async def _evaluate_sla(self, run: sa.Row[Any], org_id: uuid.UUID) -> None:
        """Judge the run once, against merged data, from the rules it pinned.

        The snapshot's rules rather than the test's: a rule edited after the
        run started describes a different test (invariant 3).
        """
        rules = [
            SlaRuleSpec.model_validate(rule)
            for rule in run.configuration_snapshot.get("slaRules", [])
        ]
        async with session_for_org(org_id) as session:
            summary = await results_service.for_run(session, run.id)
            fresh = await repo.get(session, run.id)
        result = evaluate(rules, summary, degraded=bool(fresh.degraded) if fresh else False)
        async with session_for_org(org_id) as session:
            await repo.record_sla_result(
                session,
                run.id,
                result.outcome.name,
                {
                    "detail": result.detail,
                    "rules": [
                        {
                            "name": item.name,
                            "metric": item.metric,
                            "entity": item.entity,
                            "operator": item.operator,
                            "threshold": item.threshold,
                            "actual": item.actual,
                            "verdict": item.verdict.name,
                            "detail": item.detail,
                        }
                        for item in result.rules
                    ],
                },
            )

        if result.outcome.name == "FAIL":
            # A separate event, because a breach is the thing a pipeline gates
            # on and a completion is not. A run can complete and still fail its
            # thresholds -- that is the whole point of having them.
            await announce_run(
                org_id=org_id,
                run_id=run.id,
                project_id=run.project_id,
                event=RUN_SLA_BREACHED,
                detail={"detail": result.detail},
            )

    async def _finish(self, run: sa.Row[Any], org_id: uuid.UUID, status: RunStatus) -> None:
        handles = await self._reap(run, org_id)

        async with session_for_org(org_id) as session:
            await repo.transition(
                session, run.id, expected=LIVE_RUN_STATUSES, to=status, ended=True
            )
            await repo.set_summary(
                session,
                run.id,
                {"generators": len(handles), "outcome": str(status)},
            )

        # Before the SLA verdict, which takes a settling pause: a pipeline
        # waiting to know the run is over should not wait on the judging too.
        await announce_run(
            org_id=org_id,
            run_id=run.id,
            project_id=run.project_id,
            event=RUN_COMPLETED if status is RunStatus.COMPLETED else RUN_FAILED,
            detail={"status": str(status)},
        )

        if status is RunStatus.COMPLETED:
            # Only a run that finished has a result worth judging; a failed or
            # cancelled one has nothing to compare a threshold against.
            await asyncio.sleep(METRICS_SETTLE_SECONDS)
            try:
                await self._evaluate_sla(run, org_id)
            except Exception:
                # A verdict is reporting, not execution. Losing it must not
                # unfinish a run that already completed.
                logger.exception("evaluating SLA rules for run %s failed", run.id)


async def _reconcile_forever(
    bus: RedisStreamBus, orchestrator: Orchestrator, consumer: str, stopping: asyncio.Event
) -> None:
    """One run's message is acknowledged only when that run is finished with.

    A worker that dies mid-run leaves it pending, and `reclaim_stale` hands it
    to whoever is alive -- which is the whole reason the decision is computed
    from the database rather than remembered.
    """
    tracked: dict[uuid.UUID, tuple[uuid.UUID, Delivery]] = {}

    while not stopping.is_set():
        deliveries = await bus.read(RUNS_EXECUTION, WORKER_GROUP, consumer, count=10, block_ms=1000)
        deliveries += await bus.reclaim_stale(
            RUNS_EXECUTION, WORKER_GROUP, consumer, idle=RECLAIM_AFTER
        )
        for delivery in deliveries:
            run_id = uuid.UUID(delivery.payload["runId"])
            org_id = uuid.UUID(delivery.payload["organizationId"])
            tracked[run_id] = (org_id, delivery)

        for run_id, (org_id, delivery) in list(tracked.items()):
            try:
                finished = await orchestrator.reconcile(run_id, org_id)
            except Exception:
                # One run's failure is not every run's. The message stays
                # unacknowledged, so the next tick tries again from the
                # database rather than from whatever this attempt left behind.
                WORKER_FAILURES.labels(stage="reconcile").inc()
                logger.exception("reconciling run %s failed", run_id)
                continue
            if finished:
                await bus.acknowledge(RUNS_EXECUTION, WORKER_GROUP, delivery)
                del tracked[run_id]

        # Recorded after the pass, not before: the number an alert is built
        # on has to mean "work completed", not "work attempted".
        mark_tick()

        # A tick is never abandoned half-done: the sleep is what gets cut
        # short, so a reconciliation in flight always finishes.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=TICK_SECONDS)

    logger.info(
        "reconciler stopped; %d run(s) left unacknowledged for the next worker", len(tracked)
    )


async def _serve_probes(
    bus: RedisStreamBus, orchestrator: Orchestrator, consumer: str, stopping: asyncio.Event
) -> None:
    """Answer pool diagnostics on their own task.

    The API blocks a request on this, so a probe must not wait behind a tick's
    worth of reconciliation. A probe is acknowledged once answered; an
    unanswered one is better retried than lost.
    """
    while not stopping.is_set():
        deliveries = await bus.read(
            POOL_PROBES, WORKER_GROUP, consumer, count=5, block_ms=PROBE_BLOCK_MS
        )
        for delivery in deliveries:
            runtime = delivery.payload["runtime"]
            try:
                ok, detail = await orchestrator.check_image(runtime, delivery.payload["image"])
            except Exception as exc:
                logger.exception("probing a %s pool failed", runtime)
                ok, detail = False, f"The probe itself failed: {exc}"
            await bus.announce(
                probe_channel(delivery.payload["probeId"]),
                {"ok": "true" if ok else "false", "detail": detail},
            )
            await bus.acknowledge(POOL_PROBES, WORKER_GROUP, delivery)


async def _deliver_webhooks(bus: RedisStreamBus, consumer: str, stopping: asyncio.Event) -> None:
    """Send events onward, on its own task.

    Somebody else's server answering at somebody else's pace, so it gets a
    task of its own rather than a place in a queue that also carries runs.
    Every delivery is acknowledged whatever the outcome: retries and the
    decision to give up belong to the delivery itself, and a message left
    unacknowledged would be redelivered on top of them.
    """
    while not stopping.is_set():
        deliveries = await bus.read(
            WEBHOOK_DELIVERIES, WEBHOOK_GROUP, consumer, count=20, block_ms=1000
        )
        for delivery in deliveries:
            try:
                await deliver_event(delivery.payload)
            except Exception:
                logger.exception("delivering %s failed", delivery.payload.get("event"))
            await bus.acknowledge(WEBHOOK_DELIVERIES, WEBHOOK_GROUP, delivery)


async def _maintain(stopping: asyncio.Event) -> None:
    """Clear what nothing will read again, on a slow loop of its own.

    Once an hour rather than on a schedule anybody has to install: a
    deployment should not need a cron entry to stop a table growing for ever,
    and an operator who never reads this file should still not find one.

    Failure here is logged and the loop continues. Maintenance falling behind
    is a table that is larger than it should be; maintenance taking the worker
    down is every run stopping.
    """
    while not stopping.is_set():
        try:
            removed = await purge_dead_sessions()
            if removed:
                logger.info("cleared %d finished sign-in session(s)", removed)
        except Exception:
            logger.exception("clearing finished sessions failed")
        # Interruptible, so a shutdown does not wait out the hour.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=MAINTENANCE_INTERVAL_SECONDS)


async def _ingest_metrics(bus: RedisStreamBus, consumer: str, stopping: asyncio.Event) -> None:
    """Merge sketches and write them, on its own task.

    Measurement is not execution: a batch that cannot be written is
    acknowledged as lost rather than allowed to stall reconciliation, because a
    run that generated load and lost a window is degraded reporting, not a
    failed test.
    """
    while not stopping.is_set():
        deliveries = await bus.read(
            METRICS_INGESTION,
            METRICS_GROUP,
            consumer,
            count=METRICS_BATCH,
            block_ms=METRICS_BLOCK_MS,
        )
        if not deliveries:
            continue
        payloads = [delivery.payload for delivery in deliveries]
        windows = [item for item in payloads if item.get("kind") != ERRORS_KIND]
        faults = [item for item in payloads if item.get("kind") == ERRORS_KIND]

        try:
            rows = merge_batch(windows)
            by_org: dict[uuid.UUID, list[Any]] = {}
            for row in rows:
                by_org.setdefault(row.organization_id, []).append(row)
            for org_id, owned in by_org.items():
                async with session_for_org(org_id) as session:
                    stored = await write(session, owned)
                for row in stored:
                    # Announced after the write, so a subscriber never sees a
                    # window the database does not yet have. The derived values
                    # come off this window's own merged sketch -- a percentile
                    # is computed here, never carried here.
                    await bus.announce(
                        live_channel(row.run_id),
                        {
                            "type": "metric",
                            "runId": str(row.run_id),
                            "transaction": row.transaction,
                            "windowStart": row.window_start,
                            "count": str(row.count),
                            "errorCount": str(row.error_count),
                            "p50": str(percentile(row.sketch, 50)),
                            "p95": str(percentile(row.sketch, 95)),
                            "p99": str(percentile(row.sketch, 99)),
                        },
                    )
            METRIC_WINDOWS.inc(len(rows))
        except Exception:
            WORKER_FAILURES.labels(stage="metrics").inc()
            logger.exception("writing %d metric windows failed", len(windows))

        try:
            for group in faults:
                org_id = uuid.UUID(group["organizationId"])
                async with session_for_org(org_id) as session:
                    await errors_repo.upsert(session, org_id, uuid.UUID(group["runId"]), group)
        except Exception:
            WORKER_FAILURES.labels(stage="errors").inc()
            logger.exception("writing %d error groups failed", len(faults))
        for delivery in deliveries:
            await bus.acknowledge(METRICS_INGESTION, METRICS_GROUP, delivery)


def _stop(stopping: asyncio.Event, received: signal.Signals) -> Callable[[], None]:
    def handler() -> None:
        if stopping.is_set():
            return
        logger.info("%s received; finishing the current tick", received.name)
        stopping.set()

    return handler


async def _drain(work: asyncio.Future[Any], stopping: asyncio.Event) -> None:
    """Give the loops the grace period, then stop waiting.

    Waiting forever would turn a graceful stop into a hang, and the kill that
    follows is exactly the abrupt end this is trying to avoid.
    """
    stopping.set()
    try:
        await asyncio.wait_for(asyncio.shield(work), timeout=SHUTDOWN_GRACE_SECONDS)
    except TimeoutError:
        logger.warning("loops did not stop within %ds; exiting anyway", SHUTDOWN_GRACE_SECONDS)
    except asyncio.CancelledError:
        pass
    finally:
        work.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await work


async def main() -> None:
    configure_logging(get_settings().log_level)
    # Here rather than in a migration or a setup script, so `make dev` needs no
    # extra step and a deployment needs no manual one.
    ensure_bucket()
    bus = get_bus()
    await bus.ensure_group(RUNS_EXECUTION, WORKER_GROUP)
    await bus.ensure_group(POOL_PROBES, WORKER_GROUP)
    await bus.ensure_group(METRICS_INGESTION, METRICS_GROUP)
    await bus.ensure_group(WEBHOOK_DELIVERIES, WEBHOOK_GROUP)
    orchestrator = Orchestrator()
    consumer = f"worker-{socket.gethostname()}"
    # SIGTERM is how an orchestrator asks a process to stop, and how a
    # `docker stop` begins. Setting an event rather than raising lets each loop
    # finish the tick it is in: an abandoned reconciliation is the case that
    # leaves containers nothing has recorded.
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for received in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(received, _stop(stopping, received))

    # Five loops, one process: a long reconciliation must never delay a probe,
    # a probe must never delay a run, a burst of metrics must delay neither,
    # and a webhook endpoint taking ten seconds to answer must delay nothing
    # at all.
    work = asyncio.gather(
        _reconcile_forever(bus, orchestrator, consumer, stopping),
        _maintain(stopping),
        _serve_probes(bus, orchestrator, consumer, stopping),
        _ingest_metrics(bus, consumer, stopping),
        _deliver_webhooks(bus, consumer, stopping),
        serve(SERVE_PORT, stopping),
    )
    try:
        await asyncio.wait_for(work, timeout=None if not stopping.is_set() else 0)
    finally:
        await _drain(work, stopping)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
