"""Encryption at rest for the credentials table.

The provider is a seam. v0.1 has one implementation reading a key from the
environment; Vault and KMS are later implementations and later key_ref values,
which is why the reference is stored per row rather than assumed globally.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from functools import lru_cache
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from plimsoll_api.config import get_settings

NONCE_BYTES = 12
KEY_BYTES = 32


class SecretError(Exception):
    """The key is unusable, or the ciphertext does not belong to it."""


class KeyProvider(Protocol):
    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]: ...

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes: ...


def _load(encoded_key: str, name: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(encoded_key)
    except (binascii.Error, ValueError) as exc:
        raise SecretError(f"{name} is not valid base64.") from exc
    if len(key) != KEY_BYTES:
        raise SecretError(f"{name} must decode to {KEY_BYTES} bytes, got {len(key)}.")
    return key


class LocalKeyProvider:
    """AES-256-GCM under keys supplied by the environment.

    One key encrypts; any number of retired keys still decrypt. That is what
    makes rotation possible: a key cannot be replaced if replacing it makes
    every row it wrote unreadable, and a key that cannot be replaced cannot be
    leaked safely.

    The reference is derived from the key itself, so a row records which key
    opens it without anyone having to maintain a version number by hand -- the
    kind of bookkeeping that is wrong exactly when it matters.

    The nonce is stored in front of the ciphertext: it is not secret, it must
    never repeat under one key, and keeping it with the row removes any chance
    of pairing the wrong one at decryption.
    """

    def __init__(self, encoded_key: str, retired: list[str] | None = None) -> None:
        current = _load(encoded_key, "PLIMSOLL_CREDENTIAL_KEY")
        self._current_ref = self.reference_for(current)
        self._ciphers = {self._current_ref: AESGCM(current)}
        for index, encoded in enumerate(retired or []):
            key = _load(encoded, f"PLIMSOLL_CREDENTIAL_KEYS_RETIRED[{index}]")
            self._ciphers.setdefault(self.reference_for(key), AESGCM(key))

    @staticmethod
    def reference_for(key: bytes) -> str:
        """A fingerprint, not the key. It identifies which key opens a row and
        reveals nothing that would help open it."""
        return "local:" + hashlib.sha256(b"plimsoll-key-ref" + key).hexdigest()[:16]

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        nonce = os.urandom(NONCE_BYTES)
        cipher = self._ciphers[self._current_ref]
        return nonce + cipher.encrypt(nonce, plaintext, None), self._current_ref

    PREFIX = "local:"

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes:
        if not key_ref.startswith(self.PREFIX):
            # A row encrypted by a backend this deployment does not have. Said
            # plainly rather than attempted: an authentication failure would
            # send someone looking for a corrupt row instead of a missing
            # provider.
            raise SecretError(f"No provider is configured for key reference {key_ref}.")

        nonce, body = ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:]

        named = self._ciphers.get(key_ref)
        # An unrecognised local reference is tried against every configured
        # key. That covers rows written before references identified a key,
        # and it is safe because GCM authenticates: a key that does not belong
        # raises rather than returning plausible nonsense.
        candidates = [named] if named is not None else list(self._ciphers.values())

        for cipher in candidates:
            try:
                return bytes(cipher.decrypt(nonce, body, None))
            except InvalidTag:
                # Wrong key. Trying the next is safe because GCM authenticates:
                # a key that does not belong raises rather than returning
                # plausible nonsense.
                continue

        raise SecretError(
            "The ciphertext failed authentication. If the credential key was "
            "rotated, the previous key must be listed in "
            "PLIMSOLL_CREDENTIAL_KEYS_RETIRED until the rows are re-encrypted."
        )


@lru_cache
def get_key_provider() -> KeyProvider:
    settings = get_settings()
    retired = [key.strip() for key in settings.credential_keys_retired.split(",") if key.strip()]
    return LocalKeyProvider(settings.credential_key, retired=retired)
