"""Secrets go in and never come out.

There are two decryption paths -- secret_for and secret_named -- and both
return bytes to server-side callers. No DTO in this slice carries plaintext,
which is what makes "never returned by the API" checkable rather than
aspirational.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import credentials as repo
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.credentials import CredentialCreate
from plimsoll_contracts.errors import ErrorCode


async def create(session: AsyncSession, principal: AccessClaims, body: CredentialCreate) -> Any:
    ciphertext, key_ref = get_key_provider().encrypt(body.secret.encode())
    try:
        row = await repo.insert(
            session,
            org_id=principal.organization_id,
            created_by=principal.user_id,
            name=body.name,
            kind=str(body.kind),
            ciphertext=ciphertext,
            key_ref=key_ref,
        )
    except IntegrityError as exc:
        raise PlimsollError(
            ErrorCode.CONFLICT, f"A credential named {body.name} already exists."
        ) from exc

    await audit.record(
        session,
        principal=principal,
        action="credential.created",
        entity_type="credential",
        entity_id=row.id,
        metadata={"kind": str(body.kind)},
    )
    return row


async def secret_for(session: AsyncSession, credential_id: uuid.UUID) -> bytes | None:
    material = await repo.secret_material(session, credential_id)
    if material is None:
        return None
    return get_key_provider().decrypt(material.ciphertext, material.key_ref)


async def secret_named(session: AsyncSession, name: str) -> bytes | None:
    material = await repo.by_name(session, name)
    if material is None:
        return None
    return get_key_provider().decrypt(material.ciphertext, material.key_ref)


async def delete(session: AsyncSession, principal: AccessClaims, credential_id: uuid.UUID) -> None:
    if await repo.get(session, credential_id) is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such credential.")

    in_use = await repo.referencing_repos(session, credential_id)
    if in_use:
        raise PlimsollError(
            ErrorCode.RESOURCE_IN_USE,
            "This credential is still used by a script repository.",
            {"scriptRepos": in_use},
        )

    await repo.delete(session, credential_id)
    await audit.record(
        session,
        principal=principal,
        action="credential.deleted",
        entity_type="credential",
        entity_id=credential_id,
    )
