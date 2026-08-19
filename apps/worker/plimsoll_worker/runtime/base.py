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

    async def status(self, handles: list[GeneratorHandle]) -> list[GeneratorState]: ...

    async def teardown(self, handles: list[GeneratorHandle]) -> None: ...
