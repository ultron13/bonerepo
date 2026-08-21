"""Webhook subscriptions, organisation-scoped by row-level security."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def list_for_org(session: AsyncSession) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, url, events, status, created_at FROM webhook_subscriptions "
                    "ORDER BY created_at DESC, id DESC"
                )
            )
        ).all()
    )


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    url: str,
    events: list[str],
    ciphertext: bytes,
    key_ref: str,
    created_by: uuid.UUID | None,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO webhook_subscriptions "
                "(id, organization_id, url, events, secret_ciphertext, key_ref, "
                " status, created_by) "
                "VALUES (:id, :org, :url, :events, :ciphertext, :key_ref, 'ACTIVE', :by) "
                "RETURNING id, url, events, status, created_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "url": url,
                "events": events,
                "ciphertext": ciphertext,
                "key_ref": key_ref,
                "by": created_by,
            },
        )
    ).one()


async def delete(session: AsyncSession, webhook_id: uuid.UUID) -> bool:
    result = await session.execute(
        sa.text("DELETE FROM webhook_subscriptions WHERE id = :id RETURNING id"),
        {"id": webhook_id},
    )
    return result.first() is not None


async def deliverable(session: AsyncSession, event: str) -> list[sa.Row[Any]]:
    """Active subscriptions that asked for this event.

    A family is the part before the first dot. Audit events arrive already
    prefixed -- `audit.user.deactivated` -- so `audit.*` is every action in
    the trail, which is what a SIEM wants rather than a chosen part of it.
    """
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, url, secret_ciphertext, key_ref FROM webhook_subscriptions "
                    "WHERE status = 'ACTIVE' AND (:event = ANY(events) OR "
                    "  (:family = ANY(events)))"
                ),
                {"event": event, "family": event.split(".")[0] + ".*"},
            )
        ).all()
    )


async def suspend(session: AsyncSession, webhook_id: uuid.UUID, reason: str) -> None:
    """A subscription nothing can reach is stopped rather than retried for ever.

    Left active, a dead endpoint turns every event into a queue of attempts
    that never drains, and the first thing to notice is the queue rather than
    the endpoint.
    """
    await session.execute(
        sa.text(
            "UPDATE webhook_subscriptions SET status = 'SUSPENDED', updated_at = now() "
            "WHERE id = :id"
        ),
        {"id": webhook_id},
    )
