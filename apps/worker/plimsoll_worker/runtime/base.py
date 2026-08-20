"""One interface, two implementations -- Docker now, Kubernetes with the Helm
chart. The generator image is identical in both; only the launcher differs,
which is what keeps `make dev` honest about production."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class GeneratorSpec:
    run_id: uuid.UUID
    ordinal: int
    image: str
    network: str
    environment: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    # Never None in practice: a generator is the most likely thing here to
    # exhaust a host, and an unbounded one takes the control plane down with
    # it. A pool that says nothing gets the documented default.
    memory_limit: str | None = None
    cpu_limit: float | None = None


@dataclass(frozen=True)
class GeneratorHandle:
    ordinal: int
    external_ref: str


@dataclass(frozen=True)
class GeneratorState:
    ordinal: int
    present: bool
    exit_code: int | None = None


class GeneratorRuntime(Protocol):
    async def provision(self, specs: list[GeneratorSpec]) -> list[GeneratorHandle]: ...

    async def find_by_run(self, run_id: uuid.UUID) -> list[GeneratorHandle]:
        """What this run has, according to the runtime rather than the database.

        Part of the interface rather than a Docker convenience: any runtime has
        the same gap between creating a generator and recording it, and any
        implementation has to be able to close it.
        """
        ...

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]: ...

    async def teardown(self, handles: list[GeneratorHandle]) -> None: ...
