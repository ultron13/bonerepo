import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration


async def _current_setting(session: AsyncSession) -> str | None:
    value: str | None = await session.scalar(
        sa.text("SELECT current_setting('app.current_org_id', true)")
    )
    return value


async def test_the_setting_is_applied_inside_the_transaction() -> None:
    org_id = uuid.uuid4()
    async with session_for_org(org_id) as session:
        assert await _current_setting(session) == str(org_id)


async def test_the_setting_does_not_leak_to_the_next_session() -> None:
    first = uuid.uuid4()
    async with session_for_org(first) as session:
        assert await _current_setting(session) == str(first)

    async with session_for_org(None) as session:
        assert await _current_setting(session) in ("", None)
