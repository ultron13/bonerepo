"""Credentials for the things that are not people.

A pipeline cannot hold a password, and giving it a person's means a human
credential lives in CI for ever and leaves with them. A key is scoped to what
that pipeline does, shown once, and revocable without touching anyone's
account.

Stored as a SHA-256 hash. A leaked key is revoked, never recovered -- and
because the hash is of a high-entropy secret rather than a chosen password,
a fast hash is the right one here: there is nothing to brute force.
"""

from __future__ import annotations

import hashlib
import secrets

# `plim_live_` for a real deployment, `plim_test_` for one that must never
# reach production data. The prefix is part of the secret so a key found in a
# log can be recognised for what it is and revoked without guesswork.
LIVE_PREFIX = "plim_live_"
TEST_PREFIX = "plim_test_"

# 32 bytes of randomness. The hash is only as good as what it covers.
SECRET_BYTES = 32
# Enough of the key to recognise it in a list, far too little to use.
DISPLAY_PREFIX_LENGTH = 16


def mint(environment: str) -> tuple[str, str, str]:
    """Returns the secret to show once, its hash to store, and its prefix."""
    prefix = LIVE_PREFIX if environment != "development" else TEST_PREFIX
    secret = prefix + secrets.token_hex(SECRET_BYTES)
    return secret, fingerprint(secret), secret[:DISPLAY_PREFIX_LENGTH]


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def looks_like_a_key(candidate: str) -> bool:
    """Distinguishes a key from an access token without parsing either.

    Cheap and total: a bearer value either carries one of these prefixes or it
    is a JWT, and guessing wrong costs a database lookup rather than a wrong
    answer.
    """
    return candidate.startswith((LIVE_PREFIX, TEST_PREFIX))
