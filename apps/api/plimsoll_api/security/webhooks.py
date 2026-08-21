"""Where a webhook may point, and how a delivery proves it came from here.

Two separate problems, both worth stating.

**Where it may point.** A webhook URL is supplied by a tenant and fetched by
the control plane, which is the shape of every server-side request forgery
there has ever been. The control plane sits inside a private network with a
database, an object store, and on a cloud provider a metadata endpoint that
hands out credentials to whoever asks. Refusing `169.254.169.254` in the URL
is not enough: a name resolves, and it resolves *later* than it is checked.
So the host is resolved here, every address it resolves to is checked, and the
delivery connects to an address that was checked rather than re-resolving the
name. A name that answers with a public address at validation and a private
one at delivery is the whole attack, and it has a name because it works.

**How a delivery proves it came from here.** An HMAC over the timestamp and
the body, with the timestamp inside the signature so a captured delivery
cannot be replayed later against a receiver that only checks the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
import time
from urllib.parse import urlparse

SIGNATURE_HEADER = "x-plimsoll-signature"
TIMESTAMP_HEADER = "x-plimsoll-timestamp"
DELIVERY_HEADER = "x-plimsoll-delivery"


# Everything that is not somewhere else on the internet. Loopback and
# link-local carry the metadata endpoints; the private ranges are the network
# this control plane is deployed into.
def _is_reachable_publicly(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not address.is_multicast


class WebhookRefused(Exception):
    """The URL is not somewhere this system will send anything."""


def resolved_targets(url: str, *, allow_private: bool = False) -> list[str]:
    """Every address this URL's host resolves to, checked, or an exception.

    Returned rather than discarded so the caller can connect to one of these
    instead of resolving the name again. Checking a name and then connecting
    to it are two different questions unless the answer is carried between
    them.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise WebhookRefused(
            "A webhook URL must be https. A signed delivery over plain HTTP is "
            "readable by anything on the path, and the payload describes who did "
            "what and when."
        )
    if not parsed.hostname:
        raise WebhookRefused("The webhook URL has no host.")

    try:
        found = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebhookRefused(f"The host {parsed.hostname} does not resolve.") from exc

    addresses = sorted({str(info[4][0]) for info in found})
    if not addresses:
        raise WebhookRefused(f"The host {parsed.hostname} does not resolve.")

    for candidate in addresses:
        address = ipaddress.ip_address(candidate)
        if allow_private:
            # A deployment that runs its SIEM on the same private network has
            # said so deliberately. Loopback is still refused: nothing useful
            # to a receiver lives there, and it is where the control plane's
            # own unauthenticated internals answer.
            if address.is_loopback or address.is_link_local:
                raise WebhookRefused(
                    f"The host {parsed.hostname} resolves to {candidate}, which is this "
                    "process itself or a link-local address. That is refused whatever "
                    "the configuration."
                )
            continue
        if not _is_reachable_publicly(address):
            # Named plainly: an operator pointing a webhook at something
            # internal by mistake should be told what was wrong, and somebody
            # doing it deliberately learns nothing they did not already know.
            raise WebhookRefused(
                f"The host {parsed.hostname} resolves to {candidate}, which is inside "
                "this deployment's own network. A webhook may only reach a public address."
            )
    return addresses


def sign(secret: bytes, body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    """Returns (signature, timestamp).

    The timestamp is signed rather than merely sent, so a receiver that checks
    the signature is also checking the age -- otherwise a captured delivery
    replays for as long as the secret lives.
    """
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    mac = hmac.new(secret, f"{stamp}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest(), stamp


def verify(
    secret: bytes, body: bytes, signature: str, timestamp: str, tolerance: int = 300
) -> bool:
    """What a receiver does. Here so the documented procedure is the tested one."""
    try:
        age = abs(int(time.time()) - int(timestamp))
    except ValueError:
        return False
    if age > tolerance:
        return False
    expected, _ = sign(secret, body, int(timestamp))
    return hmac.compare_digest(expected, signature)
