"""Delivering events to the places an organisation asked for them.

In the worker rather than the API, because delivery is somebody else's server
answering at somebody else's pace, and nothing about that belongs on the path
of a request that has already succeeded.

Two things here are worth stating.

**The address is re-checked at delivery, and the connection goes to the
address rather than the name.** A subscription was checked when it was
created, and DNS is free to answer differently since -- a name that was public
then and private now is exactly how a webhook becomes a way to read this
deployment's own network. Checking again and then connecting by name would
still leave the gap between the two, so the checked address is what is
connected to, with the hostname carried in the Host header and in TLS.

**A subscription nothing can reach is suspended rather than retried for
ever.** A dead endpoint otherwise turns every event into a queue of attempts
that never drains, and the first thing anybody notices is the queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.repositories import webhooks as repo
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.security.webhooks import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    WebhookRefused,
    resolved_targets,
    sign,
)

logger = logging.getLogger(__name__)

# Three tries over roughly ten seconds. A receiver that is briefly restarting
# is covered; one that is gone is not worth an hour of attempts, and the
# subscription is suspended so somebody has to look at it.
ATTEMPTS = 3
BACKOFF_SECONDS = [1.0, 3.0]
TIMEOUT_SECONDS = 10.0
# Answers that mean "not now". Anything else is the receiver saying it
# understood and refused, which retrying does not improve.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


def _url_for(address: str, url: str) -> str:
    """The same URL with the checked address in place of the name."""
    parsed = urlparse(url)
    host = f"[{address}]" if ":" in address else address
    port = f":{parsed.port}" if parsed.port else ""
    return urlunparse(parsed._replace(netloc=f"{host}{port}"))


async def _attempt(client: httpx.AsyncClient, url: str, address: str, **kwargs: Any) -> int:
    parsed = urlparse(url)
    headers = dict(kwargs.pop("headers", {}))
    # Connect to the address that was checked; tell the server, and TLS, the
    # name it is meant to be.
    headers["host"] = parsed.netloc
    response = await client.post(
        _url_for(address, url),
        headers=headers,
        extensions={"sni_hostname": parsed.hostname},
        **kwargs,
    )
    return int(response.status_code)


async def deliver(payload: dict[str, str]) -> None:
    """One event, to every subscription that asked for it."""
    org_id = uuid.UUID(payload["organizationId"])
    event = payload["event"]

    async with session_for_org(org_id) as session:
        subscriptions = await repo.deliverable(session, event)
        targets = [
            (
                row.id,
                row.url,
                get_key_provider().decrypt(bytes(row.secret_ciphertext), row.key_ref),
            )
            for row in subscriptions
        ]

    if not targets:
        return

    # Read once for the event rather than once per subscription: it is one
    # decision about this deployment, not about each receiver.
    allow_private = get_settings().webhook_allow_private_addresses

    body = json.dumps(
        {
            "event": event,
            "occurredAt": payload.get("occurredAt", ""),
            "organizationId": str(org_id),
            "entity": {
                "type": payload.get("entityType") or None,
                "id": payload.get("entityId") or None,
            },
            "actorId": payload.get("actorId") or None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    for webhook_id, url, secret in targets:
        await _deliver_one(org_id, webhook_id, url, secret, body, allow_private)


async def _deliver_one(
    org_id: uuid.UUID,
    webhook_id: Any,
    url: str,
    secret: bytes,
    body: bytes,
    allow_private: bool = False,
) -> None:
    try:
        addresses = resolved_targets(url, allow_private=allow_private)
    except WebhookRefused as exc:
        # It was acceptable when it was created and is not now. Suspending is
        # the honest response: something changed about where this points.
        logger.warning("suspending webhook %s: %s", webhook_id, exc)
        await _suspend(org_id, webhook_id, str(exc))
        return

    signature, stamp = sign(secret, body)
    headers = {
        "content-type": "application/json",
        SIGNATURE_HEADER: signature,
        TIMESTAMP_HEADER: stamp,
        DELIVERY_HEADER: str(webhook_id),
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        for attempt in range(ATTEMPTS):
            try:
                status = await _attempt(client, url, addresses[0], content=body, headers=headers)
                if status < 400:
                    return
                if status not in RETRYABLE_STATUSES:
                    logger.warning("webhook %s answered %s; not retrying", webhook_id, status)
                    return
                logger.info("webhook %s answered %s; retrying", webhook_id, status)
            except Exception as exc:
                logger.info("webhook %s could not be reached (%s)", webhook_id, exc)
            if attempt < len(BACKOFF_SECONDS):
                await asyncio.sleep(BACKOFF_SECONDS[attempt])

    logger.warning("suspending webhook %s after %d failed attempts", webhook_id, ATTEMPTS)
    await _suspend(org_id, webhook_id, "The endpoint could not be reached.")


async def _suspend(org_id: uuid.UUID, webhook_id: Any, reason: str) -> None:
    async with session_for_org(org_id) as session:
        await repo.suspend(session, webhook_id, reason)
