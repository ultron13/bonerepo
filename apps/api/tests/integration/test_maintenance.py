"""Removing what nothing will read again.

A refresh family is written on every sign-in and its history grows once per
rotation. Neither is read after the family is dead and neither was ever
deleted, so both grew with use and shrank never -- 5,231 families and 5,256
history rows on a development machine two days old, which at enterprise
sign-in rates is a table nobody chose to keep.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from plimsoll_worker.maintenance import purge_dead_sessions

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


@pytest.fixture
def sessions() -> Iterator[dict[str, uuid.UUID]]:
    """Four families: two that should go, two that must not."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    ids = {name: uuid.uuid4() for name in ("revoked_old", "expired_old", "revoked_today", "live")}
    now = datetime.now(UTC)
    rows = {
        "revoked_old": (now - timedelta(days=30), now - timedelta(days=30)),
        "expired_old": (now - timedelta(days=5), None),
        # Revoked an hour ago: revocation is the theft signal, and somebody
        # looking into one wants the family still there while they look.
        "revoked_today": (now + timedelta(days=10), now - timedelta(hours=1)),
        "live": (now + timedelta(days=10), None),
    }

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
        )
        connection.execute(
            sa.text("INSERT INTO organizations (id, name, slug) VALUES (:i, 'M', :s)"),
            {"i": org_id, "s": f"maint-{org_id.hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role) "
                "VALUES (:i, :o, :e, 'M', 'VIEWER')"
            ),
            {"i": user_id, "o": org_id, "e": f"maint-{org_id.hex[:8]}@example.com"},
        )
        for name, (expires, revoked) in rows.items():
            connection.execute(
                sa.text(
                    "INSERT INTO refresh_token_families "
                    "(id, organization_id, user_id, current_hash, expires_at, revoked_at) "
                    "VALUES (:i, :o, :u, :h, :e, :r)"
                ),
                {
                    "i": ids[name],
                    "o": org_id,
                    "u": user_id,
                    "h": f"hash-{ids[name].hex}",
                    "e": expires,
                    "r": revoked,
                },
            )
            connection.execute(
                sa.text(
                    "INSERT INTO refresh_token_history (family_id, organization_id, token_hash) "
                    "VALUES (:f, :o, :h)"
                ),
                {"f": ids[name], "o": org_id, "h": f"hist-{ids[name].hex}"},
            )
    engine.dispose()

    yield {"org": org_id, "user": user_id, **ids}

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
        )
        for statement in (
            "DELETE FROM refresh_token_history WHERE organization_id = :o",
            "DELETE FROM refresh_token_families WHERE organization_id = :o",
            "DELETE FROM users WHERE organization_id = :o",
        ):
            connection.execute(sa.text(statement), {"o": org_id})
        connection.execute(sa.text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
    engine.dispose()


def _surviving(org_id: uuid.UUID) -> set[uuid.UUID]:
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": str(org_id)}
        )
        found = set(
            connection.execute(
                sa.text("SELECT id FROM refresh_token_families WHERE organization_id = :o"),
                {"o": org_id},
            ).scalars()
        )
    engine.dispose()
    return found


async def test_finished_sessions_go_and_live_ones_stay(sessions: dict[str, uuid.UUID]) -> None:
    before = _surviving(sessions["org"])
    assert len(before) == 4, "the premise: four families were written"

    removed = await purge_dead_sessions()
    assert removed >= 2, removed

    after = _surviving(sessions["org"])
    assert sessions["revoked_old"] not in after
    assert sessions["expired_old"] not in after
    # Revoked an hour ago, so still within the week a theft investigation has.
    assert sessions["revoked_today"] in after
    assert sessions["live"] in after


async def test_history_goes_with_its_family(sessions: dict[str, uuid.UUID]) -> None:
    """History is only useful for detecting a replay inside a live family.
    It cascades, which is what stops it outliving the thing it describes."""
    await purge_dead_sessions()

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(sessions["org"])},
        )
        orphaned = connection.execute(
            sa.text(
                "SELECT count(*) FROM refresh_token_history h "
                "WHERE h.organization_id = :o AND NOT EXISTS "
                "(SELECT 1 FROM refresh_token_families f WHERE f.id = h.family_id)"
            ),
            {"o": sessions["org"]},
        ).scalar_one()
    engine.dispose()
    assert orphaned == 0


async def test_purging_twice_removes_nothing_the_second_time(
    sessions: dict[str, uuid.UUID],
) -> None:
    """Idempotent, so a maintenance loop that runs more often than expected
    costs a query rather than anything else."""
    await purge_dead_sessions()
    assert await purge_dead_sessions() == 0
