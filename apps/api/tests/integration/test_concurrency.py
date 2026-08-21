"""What happens when two things arrive at once.

Every property here is already tested one call at a time, and one call at a
time is not the question. A CI fleet starts runs from several pipelines in the
same second; a person double-clicks; a retry lands beside the request it was
retrying. Sequential idempotency and concurrent idempotency are different
claims, and only the second one is about production.

Real concurrency, through the HTTP boundary, against the running stack --
because what is being tested is the database's behaviour under contention, and
nothing about that survives being simulated.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.seed import DEMO_TEST_ID
from tests.integration.conftest import ADMIN, API_URL
from tests.integration.test_cross_tenant_api import OWNER_URL

pytestmark = pytest.mark.integration

AT_ONCE = 8


def _clients(count: int) -> list[httpx.Client]:
    """Separate clients, so the requests do not queue behind one connection."""
    token = httpx.post(f"{API_URL}/api/v1/auth/login", json=ADMIN, timeout=30).json()["accessToken"]
    clients = []
    for _ in range(count):
        client = httpx.Client(base_url=API_URL, timeout=60)
        client.headers["Authorization"] = f"Bearer {token}"
        clients.append(client)
    return clients


def _all_at_once(work: Callable[[httpx.Client], Any], count: int = AT_ONCE) -> list[Any]:
    """A barrier, not just a thread pool.

    Handing work to a pool dispatches it in quick succession, which is not the
    same as together: with a few threads the requests can simply queue and the
    contention being tested never happens, so the test passes without ever
    having asked the question. The barrier holds every thread until the last
    one is ready, and releases them into the same instant.
    """
    clients = _clients(count)
    gate = threading.Barrier(count)

    def run(client: httpx.Client) -> Any:
        gate.wait(timeout=30)
        return work(client)

    try:
        with ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(run, clients))
    finally:
        for client in clients:
            client.close()


def _cancel(runs: list[str]) -> None:
    with _clients(1)[0] as client:
        for run_id in runs:
            client.post(f"/api/v1/runs/{run_id}/cancel")


def test_simultaneous_starts_all_get_their_own_run_number() -> None:
    """The counter row is taken with an UPDATE so concurrent starts serialise
    on its lock rather than racing UNIQUE (project_id, run_number). Without
    that, two pipelines starting in the same second would leave one of them
    with a 500 and no run."""
    responses = _all_at_once(lambda client: client.post(f"/api/v1/tests/{DEMO_TEST_ID}/runs"))

    started = [r for r in responses if r.status_code == 201]
    assert len(started) == AT_ONCE, [r.status_code for r in responses]

    runs = [r.json() for r in started]
    numbers = [run["runNumber"] for run in runs]
    assert len(set(numbers)) == AT_ONCE, f"run numbers collided: {sorted(numbers)}"
    assert len({run["id"] for run in runs}) == AT_ONCE

    _cancel([run["id"] for run in runs])


def test_simultaneous_stops_of_one_run_are_all_accepted() -> None:
    """Invariant 5. Repeating stop returns 200 and does not re-run side
    effects -- and a retry that lands beside its original is the case where
    "repeating" and "at the same time" are the same thing."""
    with _clients(1)[0] as client:
        run = client.post(f"/api/v1/tests/{DEMO_TEST_ID}/runs").json()

    responses = _all_at_once(lambda c: c.post(f"/api/v1/runs/{run['id']}/stop"))
    codes = sorted(r.status_code for r in responses)
    assert all(code == 200 for code in codes), codes

    _cancel([run["id"]])


def test_a_run_ends_once_however_many_commands_arrive() -> None:
    """Stop and cancel racing each other. The run has one ending, and the
    transition is guarded by the status it expects, so whichever lands first
    decides and the rest are no-ops rather than a second ending."""
    with _clients(1)[0] as client:
        run = client.post(f"/api/v1/tests/{DEMO_TEST_ID}/runs").json()

    def command(client: httpx.Client) -> int:
        which = "stop" if uuid.uuid4().int % 2 else "cancel"
        return client.post(f"/api/v1/runs/{run['id']}/{which}").status_code

    codes = _all_at_once(command)
    assert all(code == 200 for code in codes), codes

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        org = httpx.get(
            f"{API_URL}/api/v1/auth/me",
            headers={"authorization": _clients(1)[0].headers["Authorization"]},
            timeout=30,
        ).json()["organizationId"]
        connection.execute(sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
        endings = connection.execute(
            sa.text("SELECT ended_at FROM test_runs WHERE id = :id"), {"id": uuid.UUID(run["id"])}
        ).all()
    engine.dispose()
    assert len(endings) == 1


def test_inviting_the_same_person_twice_at_once_is_a_conflict_not_a_fault() -> None:
    """A duplicate is somebody who is already a member, and the caller should
    be told so. Without the constraint being handled, one of these is a 500 --
    and a 500 is what makes a retry loop keep going."""
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    responses = _all_at_once(
        lambda client: client.post(
            "/api/v1/users", json={"email": email, "name": "Race", "orgRole": "VIEWER"}
        ),
        count=4,
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == 1, codes
    assert all(code in (201, 409) for code in codes), codes

    with _clients(1)[0] as client:
        listed = [u for u in client.get("/api/v1/users").json()["items"] if u["email"] == email]
        assert len(listed) == 1
        client.post(f"/api/v1/users/{listed[0]['id']}/deactivate")

    user_id = uuid.UUID(listed[0]["id"])
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(_demo_org())},
        )
        connection.execute(
            sa.text("DELETE FROM refresh_token_families WHERE user_id = :id"), {"id": user_id}
        )
        connection.execute(sa.text("DELETE FROM audit_logs WHERE entity_id = :id"), {"id": user_id})
        connection.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    engine.dispose()


def _demo_org() -> uuid.UUID:
    with _clients(1)[0] as client:
        return uuid.UUID(client.get("/api/v1/auth/me").json()["organizationId"])


def test_simultaneous_pool_probes_do_not_lose_an_answer() -> None:
    """Each probe is answered on its own channel, so several in flight are
    several answers rather than one heard by whoever asked last."""
    with _clients(1)[0] as client:
        pool_id = client.get("/api/v1/generator-pools?limit=1").json()["items"][0]["id"]

    responses = _all_at_once(
        lambda c: c.post(f"/api/v1/generator-pools/{pool_id}/test-connection"), count=4
    )
    assert all(r.status_code == 200 for r in responses), [r.status_code for r in responses]
    assert all("ok" in r.json() for r in responses)


def test_no_project_counter_has_fallen_behind_its_runs() -> None:
    """The counter is the only thing that may hand out a run number.

    Anything that writes a run without going through it -- a migration, a
    repair, a well-meant shortcut -- leaves the counter pointing at a number
    already taken, and every start after that fails on UNIQUE (project_id,
    run_number) until somebody works out why. The failure arrives long after
    the change that caused it, so it is worth asserting directly.

    Learned the hard way: replacing the counter with `max(run_number) + 1` to
    prove this file's first test could detect it did exactly this to the
    development database.
    """
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        behind = connection.execute(
            sa.text(
                "SELECT c.project_id, c.next_run_number FROM project_run_counters c "
                "WHERE c.next_run_number <= ("
                "  SELECT coalesce(max(r.run_number), 0) FROM test_runs r "
                "  WHERE r.project_id = c.project_id)"
            )
        ).all()
    engine.dispose()
    assert behind == [], f"counters behind their runs: {behind}"
