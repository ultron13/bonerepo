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
from typing import Any, cast

import docker
from docker.errors import DockerException, ImageNotFound, NotFound

from plimsoll_worker.runtime.base import GeneratorHandle, GeneratorSpec, GeneratorState


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
                "plimsoll.run": str(spec.run_id),
                "plimsoll.ordinal": str(spec.ordinal),
            },
            # Invariant 6. A lost generator is capacity loss and must surface as
            # such, never be papered over by a restart. Docker documents "no" as
            # the name that disables restarting; the stubs list only the names
            # that enable it, so the cast covers a stub gap, not a wrong value.
            restart_policy=cast("Any", {"Name": "no"}),
            mem_limit=spec.memory_limit,
            nano_cpus=int(spec.cpu_limit * 1_000_000_000) if spec.cpu_limit else None,
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
            all=True, filters={"label": f"plimsoll.run={run_id}"}
        )
        found = []
        for container in containers:
            ordinal = container.labels.get("plimsoll.ordinal")
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
