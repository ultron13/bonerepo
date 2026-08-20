"""A million failures are one problem repeated."""

from plimsoll_agent.errors import ErrorFolder, fingerprint
from plimsoll_agent.jtl import Sample


def _failure(message: str, code: str = "500", label: str = "Checkout", at: float = 0.0) -> Sample:
    return Sample(
        at=at, label=label, elapsed=100, success=False, response_code=code, message=message
    )


def test_the_same_fault_groups_even_when_its_text_differs() -> None:
    """Identifiers and numbers vary between occurrences of one fault; grouping
    on the raw text would report every occurrence as its own problem."""
    first = fingerprint("500", "Timeout connecting to db-node-17 after 30012ms", "Checkout")
    second = fingerprint("500", "Timeout connecting to db-node-4 after 29998ms", "Checkout")
    assert first == second


def test_a_uuid_does_not_split_a_group() -> None:
    first = fingerprint("500", "Order 3f2b1c4d-1111-2222-3333-444455556666 failed", "Checkout")
    second = fingerprint("500", "Order 99999999-aaaa-bbbb-cccc-dddddddddddd failed", "Checkout")
    assert first == second


def test_different_faults_stay_apart() -> None:
    assert fingerprint("500", "Connection refused", "Checkout") != fingerprint(
        "500", "Out of memory", "Checkout"
    )


def test_the_same_message_on_a_different_transaction_is_a_different_problem() -> None:
    """Where a fault happens is part of what it is."""
    assert fingerprint("500", "Boom", "Checkout") != fingerprint("500", "Boom", "Browse")


def test_a_different_code_is_a_different_problem() -> None:
    assert fingerprint("500", "Boom", "Checkout") != fingerprint("503", "Boom", "Checkout")


def test_a_fingerprint_is_stable_and_short() -> None:
    value = fingerprint("500", "Boom", "Checkout")
    assert value == fingerprint("500", "Boom", "Checkout")
    assert 0 < len(value) <= 128


def test_repeated_failures_become_one_group_with_a_count() -> None:
    folder = ErrorFolder()
    for index in range(50):
        folder.record(_failure(f"Timeout after {index}ms", at=float(index)))

    groups = folder.drain()
    assert len(groups) == 1
    assert groups[0]["count"] == "50"
    assert groups[0]["transaction"] == "Checkout"


def test_a_group_carries_first_and_last_seen() -> None:
    folder = ErrorFolder()
    folder.record(_failure("Boom", at=100.0))
    folder.record(_failure("Boom", at=160.0))

    group = folder.drain()[0]
    assert float(group["firstSeen"]) == 100.0
    assert float(group["lastSeen"]) == 160.0


def test_a_group_keeps_one_readable_sample() -> None:
    """A count without an example tells an operator a number and nothing they
    can act on."""
    folder = ErrorFolder()
    folder.record(_failure("Connection refused by db-node-3"))
    sample = folder.drain()[0]["sample"]
    assert "Connection refused" in sample


def test_successes_are_not_errors() -> None:
    folder = ErrorFolder()
    folder.record(
        Sample(at=0.0, label="Browse", elapsed=10, success=True, response_code="200", message="OK")
    )
    assert folder.drain() == []


def test_draining_twice_does_not_repeat_a_group() -> None:
    folder = ErrorFolder()
    folder.record(_failure("Boom"))
    assert len(folder.drain()) == 1
    assert folder.drain() == []
