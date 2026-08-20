"""What happens after a worker dies at the worst possible moment.

Containers are created and then their references are written. A worker that
stops between the two leaves containers the database has never heard of: they
are never reaped, and because their names are deterministic, the next attempt
collides with them. The run also stops moving, because nothing advances a run
that is already past QUEUED.

This reproduces that state exactly -- the run put back to ALLOCATING with its
references cleared while its containers keep running -- and asserts the worker
gets it moving again.
"""

import asyncio
import shutil
import subprocess
import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"


async def _forget_the_containers(org_id: uuid.UUID, run_id: str) -> list[str]:
    """Put the run back the way a crash mid-provision would leave it."""
    async with session_for_org(org_id) as session:
        refs = list(
            (
                await session.execute(
                    sa.text(
                        "SELECT external_ref FROM run_generators "
                        "WHERE run_id = :run AND external_ref IS NOT NULL"
                    ),
                    {"run": run_id},
                )
            ).scalars()
        )
        # Only the reference. A crash between creating a container and
        # recording it leaves the status exactly as it was -- resetting it here
        # would simulate something that cannot happen, and would test the
        # recovery against a state it will never meet.
        await session.execute(
            sa.text("UPDATE run_generators SET external_ref = NULL WHERE run_id = :run"),
            {"run": run_id},
        )
        await session.execute(
            sa.text("UPDATE test_runs SET status = 'ALLOCATING' WHERE id = :run"),
            {"run": run_id},
        )
    return refs


def _start(client: httpx.Client) -> str:
    """Synchronous on purpose: the HTTP client blocks, so it runs in a thread
    rather than on the loop the database session is using."""
    # Long enough that the run is still in flight when its references are
    # cleared. A run that finished first would have had its containers reaped,
    # and recovery would legitimately create new ones -- proving nothing.
    test_id = _short_test(client, seconds=240, users=2)
    run_id = str(client.post(f"/api/v1/tests/{test_id}/runs").json()["id"])
    _await_status(client, run_id, {"RUNNING"}, timeout=300)
    return run_id


def _settle(client: httpx.Client, run_id: str, wanted: set[str]) -> dict[str, Any]:
    return _await_status(client, run_id, wanted, timeout=180)


def _alive(refs: list[str]) -> set[str]:
    running = subprocess.run(  # noqa: S603
        [DOCKER, "ps", "-q", "--no-trunc"], capture_output=True, text=True, check=True
    ).stdout.split()
    return {ref for ref in refs if ref in running}


def _stop(client: httpx.Client, run_id: str) -> None:
    client.post(f"/api/v1/runs/{run_id}/stop")
    _await_status(client, run_id, TERMINAL, timeout=300)


async def test_a_run_abandoned_mid_provision_recovers(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    run_id = await asyncio.to_thread(_start, admin_client)

    forgotten = await _forget_the_containers(admin_org, run_id)
    assert forgotten, "the run had no containers to forget"
    # The premise, asserted rather than assumed: these containers are still
    # there to be adopted. Without this the test can pass on a run that simply
    # started over.
    assert _alive(forgotten) == set(forgotten), "the containers were gone before the test began"

    # The whole claim: the run moves again rather than sitting in ALLOCATING,
    # and it does so without creating a second container per ordinal.
    recovered = await asyncio.to_thread(_settle, admin_client, run_id, {"RUNNING", *TERMINAL})
    assert recovered["status"] in {"RUNNING", *TERMINAL}, recovered

    async with session_for_org(admin_org) as session:
        refs = list(
            (
                await session.execute(
                    sa.text("SELECT external_ref FROM run_generators WHERE run_id = :run"),
                    {"run": run_id},
                )
            ).scalars()
        )
    # Adopted, not recreated: the same containers, recorded again.
    assert set(refs) == set(forgotten), (refs, forgotten)

    await asyncio.to_thread(_stop, admin_client, run_id)
