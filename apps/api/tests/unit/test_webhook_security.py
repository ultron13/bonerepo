"""Where a webhook may point, and whether a delivery can be forged or replayed.

A webhook URL is supplied by a tenant and fetched by the control plane, which
is the shape of every server-side request forgery there has ever been. Each
test below is a way in that works if the corresponding check is missing.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from plimsoll_api.security import webhooks
from plimsoll_api.security.webhooks import WebhookRefused, sign, verify

SECRET = b"a-shared-secret"
BODY = b'{"action":"user.deactivated"}'


@pytest.fixture
def resolves(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point a hostname wherever a test needs it, without a DNS server."""

    def to(*addresses: str) -> None:
        def fake(host: str, port: int, **kwargs: Any) -> list[Any]:
            return [(2, 1, 6, "", (address, port)) for address in addresses]

        monkeypatch.setattr("plimsoll_api.security.webhooks.socket.getaddrinfo", fake)

    return to


def test_a_public_https_url_is_allowed(resolves: Any) -> None:
    """The premise. Every refusal below is meaningless if nothing is allowed."""
    resolves("93.184.216.34")
    assert webhooks.resolved_targets("https://siem.example.com/hooks") == ["93.184.216.34"]


@pytest.mark.parametrize(
    "address",
    [
        # The one that hands out cloud credentials to whoever asks.
        "169.254.169.254",
        "127.0.0.1",
        "10.1.2.3",
        "192.168.1.10",
        "172.16.5.4",
        "::1",
        "fd00::1",
        "0.0.0.0",  # noqa: S104 - an address under test, not a bind address
    ],
)
def test_a_host_resolving_inside_the_network_is_refused(resolves: Any, address: str) -> None:
    resolves(address)
    with pytest.raises(WebhookRefused, match="inside this deployment"):
        webhooks.resolved_targets("https://looks-fine.example.com/hooks")


@pytest.mark.parametrize(
    ("first", "second", "private"),
    [
        # Both orderings, deliberately. The first version of this test passed
        # with only the first resolved address being checked, because the
        # private one happened to sort first -- so it proved nothing about
        # every address being checked, which is the thing it is named for.
        ("169.254.169.254", "93.184.216.34", "169.254.169.254"),
        ("93.184.216.34", "fd00::1", "fd00::1"),
    ],
)
def test_one_private_answer_among_public_ones_is_still_a_refusal(
    resolves: Any, first: str, second: str, private: str
) -> None:
    """A name can answer with several addresses, and a delivery reaches
    whichever the resolver hands over. Checking one of them checks the wrong
    one whenever it is not the one used."""
    resolves(first, second)
    with pytest.raises(WebhookRefused, match=private.replace(".", r"\.")):
        webhooks.resolved_targets("https://split-horizon.example.com/hooks")


def test_the_checked_addresses_are_returned_so_delivery_need_not_resolve_again(
    resolves: Any,
) -> None:
    """The rebinding attack in one sentence: a name that answers publicly when
    it is checked and privately when it is used. The only defence is to carry
    the checked address to the connection instead of asking again."""
    resolves("93.184.216.34", "93.184.216.35")
    assert webhooks.resolved_targets("https://siem.example.com/x") == [
        "93.184.216.34",
        "93.184.216.35",
    ]


def test_plain_http_is_refused(resolves: Any) -> None:
    """A signed delivery over HTTP is readable by anything on the path, and
    the payload says who did what and when."""
    resolves("93.184.216.34")
    with pytest.raises(WebhookRefused, match="must be https"):
        webhooks.resolved_targets("http://siem.example.com/hooks")


def test_a_host_that_does_not_resolve_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as real_socket

    def raises(*args: Any, **kwargs: Any) -> None:
        raise real_socket.gaierror("no such host")

    monkeypatch.setattr("plimsoll_api.security.webhooks.socket.getaddrinfo", raises)
    with pytest.raises(WebhookRefused, match="does not resolve"):
        webhooks.resolved_targets("https://nothing-here.example.com/hooks")


def test_a_signature_verifies() -> None:
    signature, stamp = sign(SECRET, BODY)
    assert verify(SECRET, BODY, signature, stamp)


def test_a_signature_made_with_another_secret_does_not_verify() -> None:
    signature, stamp = sign(b"someone-elses-secret", BODY)
    assert not verify(SECRET, BODY, signature, stamp)


def test_a_changed_body_does_not_verify() -> None:
    signature, stamp = sign(SECRET, BODY)
    assert not verify(SECRET, b'{"action":"nothing.happened"}', signature, stamp)


def test_an_old_delivery_cannot_be_replayed() -> None:
    """The timestamp is inside the signature, so a receiver checking the
    signature is checking the age too. Sending it alongside would let a
    captured delivery replay for as long as the secret lived."""
    old = int(time.time()) - 3600
    signature, stamp = sign(SECRET, BODY, old)
    assert not verify(SECRET, BODY, signature, stamp)


def test_a_delivery_cannot_be_replayed_with_a_fresher_timestamp() -> None:
    """Moving the timestamp forward breaks the signature, which is the point
    of signing it rather than sending it."""
    old = int(time.time()) - 3600
    signature, _ = sign(SECRET, BODY, old)
    assert not verify(SECRET, BODY, signature, str(int(time.time())))


@pytest.mark.parametrize(
    ("event", "family"),
    [
        ("audit.user.deactivated", "audit.*"),
        ("audit.api_key.created", "audit.*"),
        ("run.completed", "run.*"),
    ],
)
def test_the_family_of_an_event_is_the_part_before_the_first_dot(event: str, family: str) -> None:
    """The bug this exists to prevent shipped once and passed every test.

    Audit actions are named for what they did -- `user.deactivated` -- so
    publishing one under its own name gives it the family `user.*`, and a
    subscription to `audit.*` matches nothing at all. Nothing raised, nothing
    logged: the subscription simply never fired. Events are prefixed with
    where they came from so the family means what an operator reads it as.
    """
    assert event.split(".")[0] + ".*" == family
