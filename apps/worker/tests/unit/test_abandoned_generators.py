"""Reaping generators whose run ended while nothing was watching.

A run ending reaps its own. This finds only the ones a worker killed
mid-flight left behind -- a container the database never recorded, that
nothing afterwards looks for, holding a deterministic name a retry of the same
ordinal would collide with. One was found seventeen hours old on a development
machine, which is how this came to exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from plimsoll_worker.maintenance import ABANDONED_AFTER, reap_abandoned_generators
from plimsoll_worker.runtime.base import GeneratorHandle


class FakeRuntime:
    def __init__(self, abandoned: list[GeneratorHandle]) -> None:
        self._abandoned = abandoned
        self.torn_down: list[GeneratorHandle] = []
        self.asked_for: timedelta | None = None

    async def abandoned(self, older_than: timedelta) -> list[GeneratorHandle]:
        self.asked_for = older_than
        return self._abandoned

    async def teardown(self, handles: list[GeneratorHandle]) -> None:
        self.torn_down.extend(handles)


async def test_an_abandoned_generator_is_removed() -> None:
    runtime = FakeRuntime([GeneratorHandle(ordinal=0, external_ref="abc")])
    assert await reap_abandoned_generators(runtime) == 1
    assert [h.external_ref for h in runtime.torn_down] == ["abc"]
    assert runtime.asked_for == ABANDONED_AFTER


async def test_nothing_abandoned_tears_nothing_down() -> None:
    """A teardown call with an empty list is a request nobody needed to make."""
    runtime = FakeRuntime([])
    assert await reap_abandoned_generators(runtime) == 0
    assert runtime.torn_down == []


async def test_a_runtime_without_the_notion_is_left_alone() -> None:
    """Kubernetes pods carry activeDeadlineSeconds and the cluster ends them,
    which is the same idea expressed by something better placed to do it."""

    class Kubernetes:
        pass

    assert await reap_abandoned_generators(Kubernetes()) == 0


@pytest.mark.parametrize(
    ("status", "finished_hours_ago", "expected"),
    [
        # Exited long enough that no live run could own it.
        ("exited", 17, True),
        # Exited, but only just: a run that is still finishing might own it.
        ("exited", 1, False),
        # Running is never touched. Ending a live generator is the
        # reconciler's job, and guessing here would stop a working test.
        ("running", 99, False),
        ("created", 99, False),
        ("paused", 99, False),
    ],
)
def test_which_containers_count_as_abandoned(
    status: str, finished_hours_ago: int, expected: bool
) -> None:
    from plimsoll_worker.runtime.docker import DockerRuntime

    finished = (datetime.now(UTC) - timedelta(hours=finished_hours_ago)).isoformat()

    class FakeContainer:
        def __init__(self) -> None:
            self.id = "container-id"
            self.status = status
            self.labels = {"plimsoll.run": "r", "plimsoll.ordinal": "0"}
            self.attrs: dict[str, Any] = {"State": {"FinishedAt": finished}}

    class FakeClient:
        containers = type("C", (), {"list": staticmethod(lambda **kwargs: [FakeContainer()])})()

    runtime = DockerRuntime.__new__(DockerRuntime)
    # Built without __init__ so no daemon is contacted: what is under test is
    # which containers the filter keeps, not how a client is made.
    object.__setattr__(runtime, "_client", FakeClient())

    found = runtime._abandoned(ABANDONED_AFTER)
    assert bool(found) is expected
