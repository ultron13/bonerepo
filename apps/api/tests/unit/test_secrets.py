import base64
import os

import pytest

from plimsoll_api.security.secrets import LocalKeyProvider, SecretError


def _provider() -> LocalKeyProvider:
    return LocalKeyProvider(base64.urlsafe_b64encode(b"k" * 32).decode())


def test_a_secret_survives_a_round_trip() -> None:
    provider = _provider()
    ciphertext, key_ref = provider.encrypt(b"ghp_notarealtoken")
    assert provider.decrypt(ciphertext, key_ref) == b"ghp_notarealtoken"


def test_the_key_reference_names_the_provider_and_the_key() -> None:
    """The provider so an unavailable backend is recognised as such, and the
    key so a row can say which one opens it -- which is what makes rotation
    something other than data loss."""
    reference = _provider().encrypt(b"x")[1]
    assert reference.startswith("local:")
    assert len(reference) > len("local:")


def test_the_same_plaintext_encrypts_differently_each_time() -> None:
    provider = _provider()
    assert provider.encrypt(b"x")[0] != provider.encrypt(b"x")[0]


def test_a_tampered_ciphertext_is_refused() -> None:
    provider = _provider()
    ciphertext, key_ref = provider.encrypt(b"secret")
    tampered = bytearray(ciphertext)
    tampered[-1] ^= 0x01
    with pytest.raises(SecretError):
        provider.decrypt(bytes(tampered), key_ref)


def test_another_key_cannot_read_it() -> None:
    ciphertext, key_ref = _provider().encrypt(b"secret")
    other = LocalKeyProvider(base64.urlsafe_b64encode(os.urandom(32)).decode())
    with pytest.raises(SecretError):
        other.decrypt(ciphertext, key_ref)


def test_an_unknown_key_reference_is_refused() -> None:
    provider = _provider()
    ciphertext, _ = provider.encrypt(b"secret")
    with pytest.raises(SecretError):
        provider.decrypt(ciphertext, "vault:prod-2")


def test_a_short_key_is_rejected_at_construction() -> None:
    with pytest.raises(SecretError):
        LocalKeyProvider(base64.urlsafe_b64encode(b"tooshort").decode())
