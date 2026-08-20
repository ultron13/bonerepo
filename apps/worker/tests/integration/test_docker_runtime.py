"""Containers, created and removed against the real daemon."""

import shutil
import subprocess
import uuid

import pytest

from plimsoll_worker.runtime.base import GeneratorSpec
from plimsoll_worker.runtime.docker import DockerRuntime

pytestmark = pytest.mark.integration

IMAGE = "ghcr.io/ultron13/generator:dev"
# Resolved once, so the calls below name a full path rather than trusting PATH.
DOCKER = shutil.which("docker") or "docker"


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


def _in_image(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [DOCKER, "run", "--rm", "--entrypoint", *command],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_generator_image_carries_jmeter() -> None:
    """The version is pinned in the image, not resolved at run time: an
    air-gapped install is a stated goal, and a run must never depend on a
    download."""
    result = _in_image("/opt/jmeter/bin/jmeter", IMAGE, "--version")
    assert result.returncode == 0, result.stderr
    assert "5.6.3" in result.stdout + result.stderr


def test_the_generator_image_has_no_git() -> None:
    """Plans arrive as a staged bundle. A git binary here would invite the
    credential that staging exists to avoid."""
    result = _in_image("sh", IMAGE, "-c", "command -v git")
    assert result.returncode != 0, f"git is present at {result.stdout.strip()}"
