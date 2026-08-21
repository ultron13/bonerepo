"""Generators as sibling containers, created through the mounted socket.

The socket is root-equivalent on the host, which is why it is mounted into the
worker and nowhere else. Docker-in-Docker would need privileges too, and buys
nothing here.

The SDK is synchronous, so every call crosses into a thread rather than
blocking the loop that is also serving heartbeats.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import docker
from docker.errors import DockerException, ImageNotFound, NotFound

from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec, GeneratorState

# Room for the JVM heap the agent asks for -- one gigabyte by default -- plus
# JMeter's own overhead and the sample buffer, with headroom. A pool driving
# more virtual users per generator raises this alongside the heap; raising one
# without the other is how a generator gets killed mid-run.
DEFAULT_MEMORY_LIMIT = "2g"
DEFAULT_CPU_LIMIT = 2.0

RUN_LABEL = "plimsoll.run"
ORDINAL_LABEL = "plimsoll.ordinal"


class DockerRuntime:
    def __init__(self) -> None:
        self._client = docker.from_env()

    def _create(self, spec: GeneratorSpec) -> str:
        container = self._client.containers.run(
            spec.image,
            detach=True,
            name=f"plimsoll-gen-{spec.run_id}-{spec.ordinal}",
            network=spec.network,
            environment=spec.environment,
            # The run and the ordinal both, so a container can be traced back
            # to the row that should describe it without parsing its name.
            labels={
                **spec.labels,
                RUN_LABEL: str(spec.run_id),
                ORDINAL_LABEL: str(spec.ordinal),
            },
            # Invariant 6. A lost generator is capacity loss and must surface as
            # such, never be papered over by a restart. Docker documents "no" as
            # the name that disables restarting; the stubs list only the names
            # that enable it, so the cast covers a stub gap, not a wrong value.
            restart_policy=cast("Any", {"Name": "no"}),
            # Bounded either way. An unbounded generator that grows into
            # the host kills the worker, the API and the database -- the
            # control plane killed by the thing it was controlling. One that
            # runs out of its own memory is capacity loss, which a run already
            # knows how to report.
            mem_limit=spec.memory_limit or DEFAULT_MEMORY_LIMIT,
            nano_cpus=int((spec.cpu_limit or DEFAULT_CPU_LIMIT) * 1_000_000_000),
        )
        return str(container.id)

    async def provision(self, specs: list[GeneratorSpec]) -> list[GeneratorHandle]:
        """All of them or none of them.

        A half-provisioned run is worse than a failed one: the containers that
        were created carry no database row yet, so nothing downstream knows to
        reap them and they outlive the run that wanted them.
        """
        handles: list[GeneratorHandle] = []
        try:
            for spec in specs:
                reference = await asyncio.to_thread(self._create, spec)
                handles.append(GeneratorHandle(ordinal=spec.ordinal, external_ref=reference))
        except Exception:
            await self.teardown(handles)
            raise
        return handles

    def _find(self, run_id: uuid.UUID) -> list[GeneratorHandle]:
        containers = self._client.containers.list(
            all=True, filters={"label": f"{RUN_LABEL}={run_id}"}
        )
        found = []
        for container in containers:
            ordinal = container.labels.get(ORDINAL_LABEL)
            if ordinal is None:
                continue
            found.append(GeneratorHandle(ordinal=int(ordinal), external_ref=str(container.id)))
        return sorted(found, key=lambda handle: handle.ordinal)

    async def find_by_run(self, run_id: uuid.UUID) -> list[GeneratorHandle]:
        """What this run actually has, asked of the runtime rather than the
        database.

        The two disagree exactly when it matters: a worker that died between
        creating a container and recording it leaves one the database has never
        heard of. Nothing reaps it, and because names are deterministic, the
        next attempt collides with it and the run cannot recover either.
        """
        return await asyncio.to_thread(self._find, run_id)

    def _abandoned(self, older_than: timedelta) -> list[GeneratorHandle]:
        cutoff = datetime.now(UTC) - older_than
        found = []
        for container in self._client.containers.list(all=True, filters={"label": RUN_LABEL}):
            if container.status in ("created", "running", "restarting", "paused"):
                # Never a live generator. A run that is still going is the
                # reconciler's to end, not this.
                continue
            finished = (container.attrs.get("State") or {}).get("FinishedAt") or ""
            try:
                ended = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ended < cutoff:
                found.append(
                    GeneratorHandle(
                        ordinal=int(container.labels.get(ORDINAL_LABEL) or 0),
                        external_ref=str(container.id),
                    )
                )
        return found

    async def abandoned(self, older_than: timedelta) -> list[GeneratorHandle]:
        """Generators that stopped long ago and were never removed.

        A run ending reaps its own, so this only ever finds the ones whose run
        ended while nothing was watching -- a worker killed mid-flight leaves a
        container the database never recorded, and nothing afterwards is
        looking for it. Found one seventeen hours old on a development
        machine, holding a name that a retry of that ordinal would collide
        with.

        Exited only, and only after long enough that no live run could own it.
        A running generator is never touched: ending one is the reconciler's
        job and guessing at it here would stop a test that was working.
        """
        return await asyncio.to_thread(self._abandoned, older_than)

    def _inspect(self, handle: GeneratorHandle) -> GeneratorState:
        try:
            container = self._client.containers.get(handle.external_ref)
        except NotFound:
            return GeneratorState(ordinal=handle.ordinal, present=False)
        container.reload()
        return GeneratorState(
            ordinal=handle.ordinal,
            present=container.status in ("created", "running", "restarting", "paused"),
            exit_code=container.attrs.get("State", {}).get("ExitCode"),
        )

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]:
        return [await asyncio.to_thread(self._inspect, handle) for handle in handles]

    def _remove(self, handle: GeneratorHandle) -> None:
        try:
            self._client.containers.get(handle.external_ref).remove(force=True)
        except (NotFound, DockerException):
            # Already gone is the outcome teardown wanted.
            pass

    async def teardown(self, handles: list[GeneratorHandle]) -> None:
        for handle in handles:
            await asyncio.to_thread(self._remove, handle)

    async def limits_of(self, handle: GeneratorHandle) -> dict[str, int]:
        """What the daemon actually applied, rather than what was asked for."""

        def read() -> dict[str, int]:
            config = self._client.containers.get(handle.external_ref).attrs["HostConfig"]
            return {
                "memory": int(config.get("Memory") or 0),
                "nano_cpus": int(config.get("NanoCpus") or 0),
            }

        return await asyncio.to_thread(read)

    async def restart_policy(self, handle: GeneratorHandle) -> str:
        def read() -> str:
            container = self._client.containers.get(handle.external_ref)
            policy = container.attrs["HostConfig"].get("RestartPolicy") or {}
            return str(policy.get("Name", ""))

        return await asyncio.to_thread(read)

    async def check(self, image: str) -> tuple[bool, str]:
        """Can this runtime create a generator right now, with this image?

        Both halves matter and fail differently: an unreachable daemon is an
        operator's problem, a missing image is a build or pull that never
        happened. Saying which one saves the guess.
        """

        def probe() -> tuple[bool, str]:
            try:
                self._client.ping()
            except DockerException as exc:
                return False, f"The container runtime is unreachable: {exc}"
            try:
                self._client.images.get(image)
            except ImageNotFound:
                return False, f"The image {image} is not present on the runtime."
            except DockerException as exc:
                return False, f"The image could not be inspected: {exc}"
            return True, f"The runtime is reachable and {image} is present."

        return await asyncio.to_thread(probe)
