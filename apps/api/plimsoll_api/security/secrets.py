"""Encryption at rest for the credentials table.

The provider is a seam. v0.1 has one implementation reading a key from the
environment; Vault and KMS are later implementations and later key_ref values,
which is why the reference is stored per row rather than assumed globally.
"""

from __future__ import annotations

import base64
import binascii
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


class LocalKeyProvider:
    """AES-256-GCM under a key supplied by the environment.

    The nonce is stored in front of the ciphertext: it is not secret, it must
    never repeat under one key, and keeping it with the row removes any chance
    of pairing the wrong one at decryption.
    """

    KEY_REF = "local:v1"

    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.urlsafe_b64decode(encoded_key)
        except (binascii.Error, ValueError) as exc:
            raise SecretError("PLIMSOLL_CREDENTIAL_KEY is not valid base64.") from exc
        if len(key) != KEY_BYTES:
            raise SecretError(
                f"PLIMSOLL_CREDENTIAL_KEY must decode to {KEY_BYTES} bytes, got {len(key)}."
            )
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: bytes) -> tuple[bytes, str]:
        nonce = os.urandom(NONCE_BYTES)
        return nonce + self._cipher.encrypt(nonce, plaintext, None), self.KEY_REF

    def decrypt(self, ciphertext: bytes, key_ref: str) -> bytes:
        if key_ref != self.KEY_REF:
            raise SecretError(f"No provider is configured for key reference {key_ref}.")
        nonce, body = ciphertext[:NONCE_BYTES], ciphertext[NONCE_BYTES:]
        try:
            return self._cipher.decrypt(nonce, body, None)
        except InvalidTag as exc:
            raise SecretError("The ciphertext failed authentication.") from exc


@lru_cache
def get_key_provider() -> KeyProvider:
    return LocalKeyProvider(get_settings().credential_key)
