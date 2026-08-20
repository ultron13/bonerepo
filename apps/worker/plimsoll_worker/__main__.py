"""The worker process: consume, reconcile, repeat.

It ships in the API's image with a different command. One codebase (ADR-0001),
one build, and a service that scales and fails on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.logging import configure_logging
from plimsoll_api.messaging import (
    POOL_PROBES,
    RUNS_EXECUTION,
    WORKER_GROUP,
    Delivery,
    RedisStreamBus,
    get_bus,
    probe_channel,
    run_channel,
)
from plimsoll_api.repositories import pools as pools_repo
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import issue_agent_token
from plimsoll_api.services import script_repos
from plimsoll_api.storage import ensure_bucket, presign_get
from plimsoll_contracts.agent import Command
from plimsoll_contracts.runs import GeneratorStatus, RunStatus
from plimsoll_worker.bundle import BundleRef, stage
from plimsoll_worker.reconciler import Decision, GeneratorRow, RunView, decide, is_silent
from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime

TICK_SECONDS = 2
# The probe consumer's own block. The API waits five seconds for an answer, so
# this stays well inside that and costs one idle connection between probes.
PROBE_BLOCK_MS = 2000
# How much longer than the run an agent's credential stays valid. It covers
# provisioning, the ramp, and the wind-down; it is not a licence to outlive the
# run by any margin that matters.
GRACE_SECONDS = 120
RECLAIM_AFTER = timedelta(seconds=60)
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


class Orchestrator:
    def __init__(self) -> None:
        self._runtime = DockerRuntime()
        self._bus = get_bus()

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
            await self._reap(run.id, org_id)
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
            moved = await repo.transition(
                session, run.id, expected=[RunStatus.QUEUED], to=RunStatus.ALLOCATING
            )
        if moved is None:
            # Another worker took it. Provisioning twice is exactly the failure
            # at-least-once delivery invites, and this is where it is refused.
            return

        image = run.configuration_snapshot.get("image") or await self._pool_image(run, org_id)
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
            )
            # An ordinal that already carries a container is never given a
            # second one, so a retried provision fills only the gaps.
            for generator in view.generators
            if generator.external_ref is None
        ]

        try:
            handles = await self._runtime.provision(specs)
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
        return await stage(run.id, plans)

    async def _pool_image(self, run: sa.Row[Any], org_id: uuid.UUID) -> str:
        async with session_for_org(org_id) as session:
            pool = await pools_repo.get(
                session, uuid.UUID(run.configuration_snapshot["workload"]["generatorPoolId"])
            )
        if pool is None:
            raise RuntimeError(f"Run {run.id} names a generator pool that no longer exists.")
        return str(pool.config["image"])

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

    async def check_image(self, image: str) -> tuple[bool, str]:
        return await self._runtime.check(image)

    async def _reap(self, run_id: uuid.UUID, org_id: uuid.UUID) -> list[GeneratorHandle]:
        """Remove every container the run created.

        Idempotent, which is what lets it run on every terminal path without
        anyone having to work out whether it already ran: a container that is
        already gone is the outcome this wanted.
        """
        async with session_for_org(org_id) as session:
            rows = await repo.generators_for(session, run_id)
        handles = [
            GeneratorHandle(ordinal=row.ordinal, external_ref=row.external_ref)
            for row in rows
            if row.external_ref
        ]
        await self._runtime.teardown(handles)
        return handles

    async def _finish(self, run: sa.Row[Any], org_id: uuid.UUID, status: RunStatus) -> None:
        handles = await self._reap(run.id, org_id)

        async with session_for_org(org_id) as session:
            await repo.transition(
                session, run.id, expected=LIVE_RUN_STATUSES, to=status, ended=True
            )
            await repo.set_summary(
                session,
                run.id,
                {"generators": len(handles), "outcome": str(status)},
            )


async def _reconcile_forever(
    bus: RedisStreamBus, orchestrator: Orchestrator, consumer: str
) -> None:
    """One run's message is acknowledged only when that run is finished with.

    A worker that dies mid-run leaves it pending, and `reclaim_stale` hands it
    to whoever is alive -- which is the whole reason the decision is computed
    from the database rather than remembered.
    """
    tracked: dict[uuid.UUID, tuple[uuid.UUID, Delivery]] = {}

    while True:
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
                logger.exception("reconciling run %s failed", run_id)
                continue
            if finished:
                await bus.acknowledge(RUNS_EXECUTION, WORKER_GROUP, delivery)
                del tracked[run_id]

        await asyncio.sleep(TICK_SECONDS)


async def _serve_probes(bus: RedisStreamBus, orchestrator: Orchestrator, consumer: str) -> None:
    """Answer pool diagnostics on their own task.

    The API blocks a request on this, so a probe must not wait behind a tick's
    worth of reconciliation. A probe is acknowledged once answered; an
    unanswered one is better retried than lost.
    """
    while True:
        deliveries = await bus.read(
            POOL_PROBES, WORKER_GROUP, consumer, count=5, block_ms=PROBE_BLOCK_MS
        )
        for delivery in deliveries:
            runtime = delivery.payload["runtime"]
            try:
                if runtime != "docker":
                    ok, detail = False, f"No runtime is implemented for {runtime}."
                else:
                    ok, detail = await orchestrator.check_image(delivery.payload["image"])
            except Exception as exc:
                logger.exception("probing a %s pool failed", runtime)
                ok, detail = False, f"The probe itself failed: {exc}"
            await bus.announce(
                probe_channel(delivery.payload["probeId"]),
                {"ok": "true" if ok else "false", "detail": detail},
            )
            await bus.acknowledge(POOL_PROBES, WORKER_GROUP, delivery)


async def main() -> None:
    configure_logging(get_settings().log_level)
    # Here rather than in a migration or a setup script, so `make dev` needs no
    # extra step and a deployment needs no manual one.
    ensure_bucket()
    bus = get_bus()
    await bus.ensure_group(RUNS_EXECUTION, WORKER_GROUP)
    await bus.ensure_group(POOL_PROBES, WORKER_GROUP)
    orchestrator = Orchestrator()
    consumer = f"worker-{socket.gethostname()}"
    # Two loops, one process: a long reconciliation must never delay a probe,
    # and a probe must never delay a run.
    await asyncio.gather(
        _reconcile_forever(bus, orchestrator, consumer),
        _serve_probes(bus, orchestrator, consumer),
    )


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
