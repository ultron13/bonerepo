"""Containers that outlived the row that should have recorded them.

The worker creates containers and then writes their references. A crash between
the two leaves containers nothing knows about: they are never reaped, and the
next attempt collides with their names, so the run cannot recover either.
"""

import uuid

import pytest

from plimsoll_worker.runtime.base import GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime

pytestmark = pytest.mark.integration

IMAGE = "ghcr.io/ultron13/generator:dev"


def _spec(run_id: uuid.UUID, ordinal: int) -> GeneratorSpec:
    return GeneratorSpec(
        run_id=run_id,
        ordinal=ordinal,
        image=IMAGE,
        network="plimsoll_default",
        environment={"PLIMSOLL_SLEEP_FOREVER": "1"},
        labels={"plimsoll.test": "true"},
    )


async def test_a_run_finds_the_containers_it_already_created() -> None:
    """The recovery this depends on: after a crash, the worker must be able to
    discover what it made from the containers themselves."""
    runtime = DockerRuntime()
    run_id = uuid.uuid4()
    handles = await runtime.provision([_spec(run_id, 0), _spec(run_id, 1)])
    try:
        found = await runtime.find_by_run(run_id)
        assert {handle.ordinal for handle in found} == {0, 1}
        assert {handle.external_ref for handle in found} == {h.external_ref for h in handles}
    finally:
        await runtime.teardown(handles)


async def test_another_run_s_containers_are_not_found() -> None:
    runtime = DockerRuntime()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    handles = await runtime.provision([_spec(mine, 0)])
    try:
        assert await runtime.find_by_run(theirs) == []
    finally:
        await runtime.teardown(handles)


async def test_a_run_with_no_containers_finds_none() -> None:
    assert await DockerRuntime().find_by_run(uuid.uuid4()) == []


async def test_provisioning_over_an_orphan_does_not_collide() -> None:
    """Names are deterministic, so a second attempt at the same ordinal hits a
    conflict. Adopting rather than recreating is what makes a run recoverable
    instead of permanently stuck."""
    runtime = DockerRuntime()
    run_id = uuid.uuid4()
    first = await runtime.provision([_spec(run_id, 0)])
    try:
        adopted = await runtime.find_by_run(run_id)
        assert [handle.external_ref for handle in adopted] == [first[0].external_ref]
    finally:
        await runtime.teardown(first)
