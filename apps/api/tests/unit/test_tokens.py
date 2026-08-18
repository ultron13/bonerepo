import uuid

import pytest

from plimsoll_api.security.tokens import (
    TokenError,
    decode_access_token,
    decode_agent_token,
    issue_access_token,
    issue_agent_token,
)


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


def test_an_agent_token_round_trips() -> None:
    run_id, org_id = uuid.uuid4(), uuid.uuid4()
    claims = decode_agent_token(
        issue_agent_token(run_id, ordinal=3, org_id=org_id, ttl_seconds=300)
    )
    assert claims.run_id == run_id
    assert claims.ordinal == 3
    assert claims.organization_id == org_id


def test_an_agent_token_opens_no_ordinary_route() -> None:
    """The two families are disjoint, and this is the direction that matters:
    an agent token reaching the API would carry a generator's credential into
    routes the run's initiator is authorised for."""
    token = issue_agent_token(uuid.uuid4(), ordinal=0, org_id=uuid.uuid4(), ttl_seconds=300)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_an_access_token_is_not_an_agent_token() -> None:
    token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "ORG_ADMIN")
    with pytest.raises(TokenError):
        decode_agent_token(token)
