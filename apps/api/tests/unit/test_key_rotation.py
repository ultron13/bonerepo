"""Rotating the key that encrypts stored credentials.

A key that cannot be rotated is a key that cannot be leaked safely. Compliance
regimes ask for periodic rotation, and an incident asks for it immediately --
and until now changing PLIMSOLL_CREDENTIAL_KEY made every stored credential
permanently unreadable, because a row's key_ref was a constant that the
provider refused to look past.
"""

import base64
import os

import pytest

from plimsoll_api.security.secrets import LocalKeyProvider, SecretError


def _key() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def test_a_secret_round_trips_under_one_key() -> None:
    provider = LocalKeyProvider(_key())
    ciphertext, ref = provider.encrypt(b"hunter2")
    assert provider.decrypt(ciphertext, ref) == b"hunter2"


def test_a_key_reference_names_the_key_that_made_it() -> None:
    """Two different keys must produce two different references, or a row
    cannot say which key it belongs to and rotation has nothing to work with."""
    first, second = LocalKeyProvider(_key()), LocalKeyProvider(_key())
    assert first.encrypt(b"x")[1] != second.encrypt(b"x")[1]
    # And the same key always produces the same reference.
    assert first.encrypt(b"x")[1] == first.encrypt(b"y")[1]


def test_a_retired_key_still_decrypts_what_it_encrypted() -> None:
    """The whole point. After rotation the old rows are still readable, so
    they can be re-encrypted rather than lost."""
    old_key, new_key = _key(), _key()
    before = LocalKeyProvider(old_key)
    ciphertext, ref = before.encrypt(b"still-needed")

    after = LocalKeyProvider(new_key, retired=[old_key])
    assert after.decrypt(ciphertext, ref) == b"still-needed"


def test_new_secrets_use_the_current_key_not_a_retired_one() -> None:
    old_key, new_key = _key(), _key()
    after = LocalKeyProvider(new_key, retired=[old_key])
    _, ref = after.encrypt(b"fresh")
    assert ref == LocalKeyProvider(new_key).encrypt(b"fresh")[1]


def test_a_key_that_was_never_configured_cannot_decrypt() -> None:
    """A wrong key fails loudly. AES-GCM authenticates, so there is no quiet
    path where the wrong key returns plausible nonsense."""
    ciphertext, ref = LocalKeyProvider(_key()).encrypt(b"secret")
    with pytest.raises(SecretError):
        LocalKeyProvider(_key()).decrypt(ciphertext, ref)


def test_a_row_written_before_references_were_versioned_still_opens() -> None:
    """Rows already in the table say `local:v1`, which named no particular key.

    Refusing them would make this change the very outage it exists to prevent,
    so an unrecognised reference falls back to trying the configured keys --
    safe, because authentication means a wrong key raises rather than lying.
    """
    key = _key()
    provider = LocalKeyProvider(key)
    ciphertext, _ = provider.encrypt(b"legacy")
    assert provider.decrypt(ciphertext, "local:v1") == b"legacy"
