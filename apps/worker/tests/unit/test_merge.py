"""Two generators, one window, one merged row."""

from plimsoll_contracts.metrics import encode_sketch, new_sketch, percentile
from plimsoll_worker.metrics import merge_batch


def _window(ordinal: int, values: list[int], transaction: str = "Browse") -> dict[str, str]:
    sketch = new_sketch()
    for value in values:
        sketch.record_value(value)
    return {
        "runId": "11111111-1111-1111-1111-111111111111",
        "organizationId": "22222222-2222-2222-2222-222222222222",
        "ordinal": str(ordinal),
        "transaction": transaction,
        "windowStart": "2026-08-20T12:00:00+00:00",
        "count": str(len(values)),
        "errorCount": "0",
        "min": str(min(values)),
        "max": str(max(values)),
        "total": str(sum(values)),
        "sketch": encode_sketch(sketch),
    }


def test_two_generators_in_one_window_become_one_row() -> None:
    """Ordinal is deliberately not part of the key: that is the merge."""
    merged = merge_batch([_window(0, [100] * 10), _window(1, [200] * 10)])
    assert len(merged) == 1
    assert merged[0].count == 20
    assert merged[0].minimum == 100
    assert merged[0].maximum == 200


def test_the_merged_percentile_comes_from_the_merged_distribution() -> None:
    """Not from either generator's, and not from the average of theirs."""
    merged = merge_batch([_window(0, [100] * 1000), _window(1, [5000] * 1000)])
    p95 = percentile(merged[0].sketch, 95)
    assert p95 >= 4900, p95


def test_different_transactions_stay_separate() -> None:
    merged = merge_batch(
        [_window(0, [100], transaction="Browse"), _window(0, [900], transaction="Checkout")]
    )
    assert {row.transaction for row in merged} == {"Browse", "Checkout"}


def test_different_windows_stay_separate() -> None:
    later = _window(0, [100])
    later["windowStart"] = "2026-08-20T12:00:05+00:00"
    merged = merge_batch([_window(0, [100]), later])
    assert len(merged) == 2


def test_errors_are_summed_across_generators() -> None:
    first, second = _window(0, [100]), _window(1, [100])
    first["errorCount"] = "3"
    second["errorCount"] = "4"
    assert merge_batch([first, second])[0].error_count == 7


def test_the_organisation_comes_from_the_message_the_api_stamped() -> None:
    """The API rewrites it from the token before publishing, so by the time a
    row is built the value has already left the agent's control."""
    merged = merge_batch([_window(0, [100])])
    assert str(merged[0].organization_id) == "22222222-2222-2222-2222-222222222222"
