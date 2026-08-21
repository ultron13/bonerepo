"""Managing where this system sends what happened."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response

from plimsoll_api.config import get_settings
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import webhooks as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.security.webhooks import WebhookRefused, resolved_targets
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import Page
from plimsoll_contracts.webhooks import Webhook, WebhookCreated, WebhookInput

# Administrative: a subscription is a copy of the audit trail leaving the
# building, and the trail names people.
router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["webhooks"],
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)


def _response(row: Any) -> Webhook:
    return Webhook(
        id=str(row.id),
        url=row.url,
        events=list(row.events),
        status=row.status,
        created_at=row.created_at,
    )


@router.post("", response_model=WebhookCreated, status_code=201)
async def create_webhook(
    body: WebhookInput, principal: CurrentPrincipal, session: TenantSession
) -> WebhookCreated:
    """The URL is resolved and every address it answers with is checked.

    A webhook URL is supplied here and fetched by the control plane, which is
    the shape of every server-side request forgery there has ever been --
    refusing the obvious literals is not enough, because a name resolves, and
    it resolves after it is checked.
    """
    try:
        resolved_targets(body.url, allow_private=get_settings().webhook_allow_private_addresses)
    except WebhookRefused as exc:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, str(exc)) from exc

    secret = body.secret or secrets.token_urlsafe(32)
    ciphertext, key_ref = get_key_provider().encrypt(secret.encode())
    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        url=body.url,
        events=list(body.events),
        ciphertext=ciphertext,
        key_ref=key_ref,
        created_by=principal.user_id,
    )
    await audit.record(
        session,
        principal=principal,
        action="webhook.created",
        entity_type="webhook",
        entity_id=row.id,
        # The URL and what it subscribes to. Never the secret.
        metadata={"url": body.url, "events": list(body.events)},
    )
    return WebhookCreated(**_response(row).model_dump(by_alias=False), secret=secret)


@router.get("", response_model=Page[Webhook])
async def list_webhooks(session: TenantSession) -> Page[Webhook]:
    return Page(items=[_response(row) for row in await repo.list_for_org(session)])


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    """Idempotent: removing what is already gone is the outcome wanted."""
    removed = await repo.delete(session, webhook_id)
    if removed:
        await audit.record(
            session,
            principal=principal,
            action="webhook.deleted",
            entity_type="webhook",
            entity_id=webhook_id,
        )
    return Response(status_code=204)
