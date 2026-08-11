import pytest

from plimsoll_contracts.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    decode_cursor,
    encode_cursor,
)


def test_cursor_round_trips() -> None:
    payload = {"created_at": "2026-08-10T10:00:00Z", "id": "abc"}
    assert decode_cursor(encode_cursor(payload)) == payload


def test_cursor_is_opaque() -> None:
    encoded = encode_cursor({"id": "abc"})
    assert "abc" not in encoded


def test_malformed_cursor_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor")


def test_limits() -> None:
    assert DEFAULT_PAGE_LIMIT == 50
    assert MAX_PAGE_LIMIT == 200
