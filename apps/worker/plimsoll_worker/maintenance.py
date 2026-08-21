"""Removing what nothing will read again.

Two tables accumulate on every sign-in and were never cleared. A refresh
family is created each time somebody signs in and lives fourteen days; its
history grows once per rotation. Neither is read after the family is dead, and
neither was ever deleted -- so both grow with use and shrink never, which on a
long-lived instance is a table nobody chose to keep.

Deliberately not doing this to `audit_logs`. They are a compliance record, and
deleting one by default is a worse failure than the disk it costs: an
organisation that needed seven years and got ninety days finds out at exactly
the wrong moment. Retention there is opt-in, and the webhook export exists so
the durable copy can live in a SIEM that is built for keeping things.

Metrics are handled by TimescaleDB's own retention policy rather than here,
because dropping a chunk is not the same as deleting rows and the database is
better at knowing which chunks are whole.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

logger = logging.getLogger(__name__)


# A revoked family is kept briefly rather than removed at once: revocation is
# the theft signal, and somebody looking into one wants the family still there
# while they look.
async def purge_dead_sessions() -> int:
    """Delete finished refresh families and their history. Returns the count.

    Through a `SECURITY DEFINER` function, because the work crosses every
    organisation and row-level security is forced on both tables. The
    alternative was a superuser connection living in the worker for the sake
    of one DELETE; the function can do exactly this and nothing else, and the
    grace periods are inside it rather than passed to it.
    """
    async with session_for_org(None) as session:
        removed = await session.scalar(sa.text("SELECT maintenance_purge_dead_sessions()"))
    return int(removed or 0)
