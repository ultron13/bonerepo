from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from plimsoll_api.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session_for_org(org_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    """One transaction per request, with the tenant setting applied inside it.

    set_config(..., true) is transaction-local, so a pooled connection cannot
    carry the value into the next request. It is used in preference to
    SET LOCAL because SET does not accept bind parameters.
    """
    async with _session_factory()() as session, session.begin():
        if org_id is not None:
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": str(org_id)},
            )
        yield session
