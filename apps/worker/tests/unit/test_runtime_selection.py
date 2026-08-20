"""Which runtime a run uses, and when that is decided.

The answer has to be "the one it was started with". A pool is configuration
and configuration moves; a run in flight is an experiment already underway.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from plimsoll_worker.__main__ import Orchestrator


class FakeRuntime:
    def __init__(self, name: str) -> None:
        self.name = name
        self.torn_down: list[Any] = []

    async def teardown(self, handles: list[Any]) -> None:
        self.torn_down.append(handles)


def _run(pinned: dict[str, Any] | None) -> Any:
    snapshot: dict[str, Any] = {"workload": {"generatorPoolId": str(uuid.uuid4())}}
    if pinned is not None:
        snapshot["pool"] = pinned
    return SimpleNamespace(id=uuid.uuid4(), configuration_snapshot=snapshot)


@pytest.fixture
def orchestrator(monkeypatch: pytest.MonkeyPatch) -> Orchestrator:
    monkeypatch.setattr(
        "plimsoll_worker.__main__.RUNTIMES",
        {"docker": lambda: FakeRuntime("docker"), "kubernetes": lambda: FakeRuntime("kubernetes")},
    )
    # __init__ builds no clients, so nothing here reaches a daemon or a cluster.
    return Orchestrator.__new__(Orchestrator)


async def test_a_run_uses_the_runtime_it_pinned(orchestrator: Orchestrator) -> None:
    orchestrator._runtimes = {}
    run = _run({"runtime": "kubernetes", "image": "generator:1"})
    runtime = await orchestrator._runtime_for(run, uuid.uuid4())
    assert runtime.name == "kubernetes"


async def test_a_pool_switched_mid_run_does_not_strand_its_generators(
    orchestrator: Orchestrator,
) -> None:
    """The failure this prevents: teardown looking in the runtime the pool
    names now, finding nothing, and leaving the old generators running against
    somebody's system for as long as the container lives."""
    orchestrator._runtimes = {}
    run = _run({"runtime": "docker", "image": "generator:1"})

    # The pool has since been switched. The run must not notice.
    runtime = await orchestrator._runtime_for(run, uuid.uuid4())
    assert runtime.name == "docker"


async def test_a_runtime_is_built_once_and_reused(orchestrator: Orchestrator) -> None:
    """A client carries a connection pool; one per run leaks sockets for as
    long as the worker lives."""
    orchestrator._runtimes = {}
    first = orchestrator.runtime("docker")
    assert orchestrator.runtime("docker") is first


async def test_an_unimplemented_runtime_says_so(orchestrator: Orchestrator) -> None:
    orchestrator._runtimes = {}
    with pytest.raises(RuntimeError, match="No runtime is implemented for nomad"):
        orchestrator.runtime("nomad")
