import pytest

from plimsoll_api.errors import PlimsollError
from plimsoll_api.services.target_policy import matches_allowlist, validate_entries

ALLOWLIST = ["demo-target", ".example.com", "10.0.0.0/8"]


@pytest.mark.parametrize(
    "host", ["demo-target", "api.example.com", "deep.api.example.com", "10.4.2.1"]
)
def test_a_permitted_host_matches(host: str) -> None:
    assert matches_allowlist(host, ALLOWLIST)


@pytest.mark.parametrize(
    "host",
    ["demo-target.evil.com", "example.com.evil.net", "notexample.com", "11.0.0.1", "127.0.0.1"],
)
def test_an_unpermitted_host_does_not_match(host: str) -> None:
    assert not matches_allowlist(host, ALLOWLIST)


def test_the_bare_suffix_domain_matches_itself() -> None:
    assert matches_allowlist("example.com", [".example.com"])


def test_an_empty_allowlist_permits_nothing() -> None:
    assert not matches_allowlist("demo-target", [])


def test_matching_is_case_insensitive() -> None:
    assert matches_allowlist("API.Example.COM", ALLOWLIST)


@pytest.mark.parametrize(
    "entry", ["127.0.0.1", "localhost", "169.254.169.254", "169.254.0.0/16", "::1", "0.0.0.0/0"]
)
def test_a_dangerous_entry_is_refused(entry: str) -> None:
    with pytest.raises(PlimsollError):
        validate_entries([entry])


@pytest.mark.parametrize("entry", ["", "  ", "http://example.com", "example.com/path"])
def test_a_malformed_entry_is_refused(entry: str) -> None:
    with pytest.raises(PlimsollError):
        validate_entries([entry])


def test_a_reasonable_allowlist_is_accepted() -> None:
    validate_entries(ALLOWLIST)
