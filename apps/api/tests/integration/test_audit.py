"""An audited change and the record of it commit together or not at all."""

import uuid

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit

pytestmark = pytest.mark.integration

ORG = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.plimsoll.dev")


def _principal() -> AccessClaims:
    return AccessClaims(user_id=uuid.uuid4(), organization_id=ORG, role="ORG_ADMIN")


async def _count(action: str, entity_id: uuid.UUID) -> int:
    async with session_for_org(ORG) as session:
        count: int = await session.scalar(
            sa.text(
                "SELECT count(*) FROM audit_logs WHERE action = :action AND entity_id = :entity"
            ),
            {"action": action, "entity": entity_id},
        )
    return count


async def test_a_recorded_action_is_readable_after_commit() -> None:
    entity = uuid.uuid4()
    async with session_for_org(ORG) as session:
        await audit.record(
            session,
            principal=_principal(),
            action="project.created",
            entity_type="project",
            entity_id=entity,
            metadata={"name": "Audited"},
        )

    assert await _count("project.created", entity) == 1


async def test_a_rolled_back_change_leaves_no_audit_row() -> None:
    entity = uuid.uuid4()
    with pytest.raises(RuntimeError):
        async with session_for_org(ORG) as session:
            await audit.record(
                session,
                principal=_principal(),
                action="project.created",
                entity_type="project",
                entity_id=entity,
            )
            raise RuntimeError("the change failed after the audit row was written")

    assert await _count("project.created", entity) == 0
