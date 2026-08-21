"""Every event this system offers to subscribe to must be one it can send.

This exists because the opposite shipped. Three event names were accepted by
the API and published by nothing: a subscription to `run.completed` was
created, stored, listed, and never fired. Nothing raised and nothing logged,
because there is nothing to raise -- an event that is never produced simply
does not arrive, and no amount of testing the delivery path finds it.

It had already happened once in another form -- `audit.*` matching nothing
because audit actions carry their own families -- and was fixed there without
the general case being asked. So the general case is asked here: the names in
the contract and the names in the code are checked against each other, which
is the only thing that catches the next one.
"""

from __future__ import annotations

import pathlib
import typing

from plimsoll_contracts.webhooks import EventName

ROOT = pathlib.Path(__file__).resolve().parents[4]
SOURCE = [
    ROOT / "apps" / "api" / "plimsoll_api",
    ROOT / "apps" / "worker" / "plimsoll_worker",
]


def _published_strings() -> str:
    """Everything the publishing code says, as one haystack.

    The premise is asserted: this test looks for absence, and a path that
    reads nothing finds every name absent -- or, once the names happen to
    match, finds nothing at all and passes. Both are worse than failing.
    """
    text = ""
    for tree in SOURCE:
        assert tree.is_dir(), f"{tree} is not where this test thinks it is"
        for path in tree.rglob("*.py"):
            text += path.read_text()
    assert len(text) > 10_000, "the source trees read empty; the paths have moved"
    return text


def test_every_advertised_event_is_produced_somewhere() -> None:
    advertised = set(typing.get_args(EventName))
    assert advertised, "the contract advertises nothing, which is its own bug"

    source = _published_strings()
    missing = []
    for name in sorted(advertised):
        if name.endswith(".*"):
            # A family: something must publish an event under that prefix.
            if f'"{name[:-1]}' not in source and f'f"{name[:-1]}' not in source:
                missing.append(name)
        elif f'"{name}"' not in source:
            missing.append(name)

    assert missing == [], (
        f"advertised but never published: {missing}. A subscription to one of "
        "these is accepted, stored, listed, and never fires."
    )
