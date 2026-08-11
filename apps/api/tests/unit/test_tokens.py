import uuid

import pytest

from plimsoll_api.security.tokens import TokenError, decode_access_token, issue_access_token


def test_round_trip_preserves_the_principal() -> None:
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    claims = decode_access_token(issue_access_token(user_id, org_id, "ORG_ADMIN"))
    assert claims.user_id == user_id
    assert claims.organization_id == org_id
    assert claims.role == "ORG_ADMIN"


def test_a_tampered_token_is_rejected() -> None:
    token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER")
    head, payload, signature = token.split(".")
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{payload}.{signature[:-2]}xx")


def test_an_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLIMSOLL_ACCESS_TOKEN_TTL_SECONDS", "-1")
    from plimsoll_api.config import get_settings

    get_settings.cache_clear()
    try:
        token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER")
        with pytest.raises(TokenError):
            decode_access_token(token)
    finally:
        get_settings.cache_clear()


def test_a_token_signed_with_another_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from plimsoll_api.config import get_settings

    token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER")
    monkeypatch.setenv("PLIMSOLL_JWT_SECRET", "a-different-secret-of-sufficient-length")
    get_settings.cache_clear()
    try:
        with pytest.raises(TokenError):
            decode_access_token(token)
    finally:
        get_settings.cache_clear()
