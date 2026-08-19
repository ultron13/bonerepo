"""Containers, created and removed against the real daemon."""

import uuid

import pytest

from plimsoll_worker.runtime.base import GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime

pytestmark = pytest.mark.integration

IMAGE = "ghcr.io/ultron13/generator:dev"


def _spec(ordinal: int) -> GeneratorSpec:
    return GeneratorSpec(
        run_id=uuid.uuid4(),
        ordinal=ordinal,
        image=IMAGE,
        network="plimsoll_default",
        environment={"PLIMSOLL_SLEEP_FOREVER": "1"},
        labels={"plimsoll.test": "true"},
    )


async def test_a_generator_is_created_and_removed() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    try:
        assert len(handles) == 1
        assert handles[0].external_ref
        statuses = await runtime.status(handles)
        assert statuses[0].present is True
    finally:
        await runtime.teardown(handles)

    assert (await runtime.status(handles))[0].present is False


async def test_a_generator_never_restarts() -> None:
    """Invariant 6: a restarted generator resets virtual-user state mid-test
    and silently corrupts the run."""
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    try:
        assert await runtime.restart_policy(handles[0]) in ("", "no")
    finally:
        await runtime.teardown(handles)


async def test_tearing_down_twice_is_not_an_error() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0)])
    await runtime.teardown(handles)
    await runtime.teardown(handles)


async def test_every_generator_gets_its_own_container() -> None:
    runtime = DockerRuntime()
    handles = await runtime.provision([_spec(0), _spec(1)])
    try:
        assert len({handle.external_ref for handle in handles}) == 2
    finally:
        await runtime.teardown(handles)
