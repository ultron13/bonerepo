from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.messaging import POOL_PROBES, get_bus, probe_channel
from plimsoll_api.repositories import pools as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pools import PoolCreate, PoolUpdate, ProbeResult

UPDATABLE = {"name", "config", "region", "max_generators", "max_vus_per_generator"}
# Short by design: this is a diagnostic behind a request, and a slow answer
# is worse than a clear 'nobody replied'.
PROBE_TIMEOUT_SECONDS = 5


async def create(session: AsyncSession, principal: AccessClaims, body: PoolCreate) -> Any:
    try:
        row = await repo.insert(
            session,
            org_id=principal.organization_id,
            name=body.name,
            runtime=str(body.runtime),
            config=body.config,
            region=body.region,
            max_generators=body.max_generators,
            max_vus_per_generator=body.max_vus_per_generator,
        )
    except IntegrityError as exc:
        raise PlimsollError(
            ErrorCode.CONFLICT, f"A generator pool named {body.name} already exists."
        ) from exc

    await audit.record(
        session,
        principal=principal,
        action="pool.created",
        entity_type="generator_pool",
        entity_id=row.id,
        metadata={"runtime": str(body.runtime)},
    )
    return row


async def require(session: AsyncSession, pool_id: uuid.UUID) -> Any:
    row = await repo.get(session, pool_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such generator pool.")
    return row


async def update(
    session: AsyncSession, principal: AccessClaims, pool_id: uuid.UUID, body: PoolUpdate
) -> Any:
    current = await require(session, pool_id)
    changes = {
        column: value
        for column, value in body.model_dump(exclude_unset=True).items()
        if column in UPDATABLE
    }
    if not changes:
        return current

    row = await repo.update(session, pool_id, changes)
    await audit.record(
        session,
        principal=principal,
        action="pool.updated",
        entity_type="generator_pool",
        entity_id=pool_id,
        metadata={"fields": sorted(changes)},
    )
    return row


async def archive(session: AsyncSession, principal: AccessClaims, pool_id: uuid.UUID) -> None:
    await require(session, pool_id)
    if await repo.archive(session, pool_id):
        await audit.record(
            session,
            principal=principal,
            action="pool.deleted",
            entity_type="generator_pool",
            entity_id=pool_id,
        )


async def capacity_for(session: AsyncSession, pool_id: uuid.UUID) -> int:
    """Free capacity: what the pool can supply, less what active runs hold."""
    total = await repo.capacity(session, pool_id)
    if total is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such active generator pool.")
    return total - await repo.committed_users(session, pool_id)


async def test_connection(session: AsyncSession, pool_id: uuid.UUID) -> ProbeResult:
    """Can this pool actually create generators, and is its image present?

    Answered by the worker, because the worker is the only process that holds a
    container runtime. Giving the API a Docker socket to answer a diagnostic
    would hand root-equivalent host access to the process that serves untrusted
    requests -- a bad trade for a convenience.
    """
    pool = await require(session, pool_id)
    probe_id = uuid.uuid4()
    bus = get_bus()

    # Subscribed before the publish, so a fast worker cannot answer into a
    # channel nobody is listening on yet.
    async with bus.listen(probe_channel(probe_id)) as replies:
        await bus.publish(
            POOL_PROBES,
            {
                "probeId": str(probe_id),
                "runtime": pool.runtime,
                "image": str(pool.config.get("image", "")),
            },
        )
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                async for reply in replies:
                    return ProbeResult(ok=reply["ok"] == "true", detail=reply["detail"])
        except TimeoutError:
            pass

    return ProbeResult(ok=False, detail="No worker answered. The execution plane may be down.")
