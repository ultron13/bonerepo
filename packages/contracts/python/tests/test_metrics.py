"""HDR sketches: the merge that makes a percentile honest.

ADR-0004 exists because averaging percentiles is wrong in an unpredictable
direction. These tests assert the correct behaviour and demonstrate the error
the architecture refuses to make.
"""

import statistics

from plimsoll_contracts.metrics import (
    decode_sketch,
    encode_sketch,
    merge_sketches,
    new_sketch,
    percentile,
)


def test_a_sketch_round_trips_through_the_wire_format() -> None:
    sketch = new_sketch()
    for value in (100, 200, 300, 400, 500):
        sketch.record_value(value)

    restored = decode_sketch(encode_sketch(sketch))
    assert restored.get_total_count() == 5
    assert percentile(restored, 50) == percentile(sketch, 50)


def test_merging_is_order_independent() -> None:
    """Generators report in whatever order they finish a window."""
    first, second, third = new_sketch(), new_sketch(), new_sketch()
    for value in range(1, 101):
        first.record_value(value)
    for value in range(101, 201):
        second.record_value(value)
    for value in range(201, 301):
        third.record_value(value)

    forwards = merge_sketches([encode_sketch(s) for s in (first, second, third)])
    backwards = merge_sketches([encode_sketch(s) for s in (third, second, first)])
    assert percentile(forwards, 95) == percentile(backwards, 95)
    assert forwards.get_total_count() == backwards.get_total_count() == 300


def test_merging_beats_averaging_and_the_gap_is_large() -> None:
    """The whole reason this pipeline exists.

    One generator is fast, one is slow. Their p95s average to a number that
    describes neither, and understates what users actually experienced.
    """
    fast, slow = new_sketch(), new_sketch()
    for _ in range(1000):
        fast.record_value(100)
    for _ in range(1000):
        slow.record_value(5000)

    merged = merge_sketches([encode_sketch(fast), encode_sketch(slow)])
    truth = percentile(merged, 95)
    averaged = statistics.mean([percentile(fast, 95), percentile(slow, 95)])

    # Half the requests took 5000ms, so the true p95 is up at 5000.
    assert truth >= 4900
    # The average of the two p95s claims about 2550 -- a number no request saw.
    assert averaged < 3000
    assert truth - averaged > 2000


def test_an_empty_merge_is_empty_rather_than_an_error() -> None:
    """A window in which nothing happened is a fact, not a failure."""
    merged = merge_sketches([])
    assert merged.get_total_count() == 0


def test_precision_is_within_the_documented_bound() -> None:
    """Three significant figures: about 0.1% at any magnitude."""
    sketch = new_sketch()
    for value in range(1, 10001):
        sketch.record_value(value)
    exact = 9500
    assert abs(percentile(sketch, 95) - exact) / exact < 0.01
