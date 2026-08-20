"""Folding samples into one sketch per transaction per window."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from hdrh.histogram import HdrHistogram

from plimsoll_agent.jtl import Sample
from plimsoll_contracts.metrics import WINDOW_SECONDS, SketchWindow, encode_sketch, new_sketch


def window_of(at: float) -> int:
    return int(at // WINDOW_SECONDS) * WINDOW_SECONDS


@dataclass
class _Bucket:
    sketch: HdrHistogram = field(default_factory=new_sketch)
    count: int = 0
    error_count: int = 0
    minimum: int = 0
    maximum: int = 0
    total: int = 0


class Folder:
    """One per generator.

    Bandwidth is a function of transaction count, not request count, which is
    what keeps this flat as load grows: a million samples in a window still
    leave as one sketch.
    """

    def __init__(self, run_id: str, ordinal: int) -> None:
        self._run_id = run_id
        self._ordinal = ordinal
        self._buckets: dict[tuple[int, str], _Bucket] = {}

    def record(self, sample: Sample) -> None:
        key = (window_of(sample.at), sample.label)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket()
        # An error still took time. Excluding it would flatter the result by
        # dropping exactly the slow requests that caused the failure.
        bucket.sketch.record_value(max(sample.elapsed, 1))
        bucket.count += 1
        bucket.total += sample.elapsed
        bucket.maximum = max(bucket.maximum, sample.elapsed)
        bucket.minimum = (
            sample.elapsed if bucket.count == 1 else min(bucket.minimum, sample.elapsed)
        )
        if not sample.success:
            bucket.error_count += 1

    def drain(self, now: float) -> list[SketchWindow]:
        """Only windows that have closed.

        A window still being written would be shipped, then shipped again when
        the rest of it arrived, and the two would merge into a window counting
        its early samples twice. `now = inf` closes everything, which is what
        the end of a run wants.
        """
        closed = [key for key in self._buckets if key[0] + WINDOW_SECONDS <= now]
        drained = []
        for key in sorted(closed):
            bucket = self._buckets.pop(key)
            window_start, transaction = key
            drained.append(
                SketchWindow(
                    run_id=self._run_id,
                    ordinal=self._ordinal,
                    transaction=transaction,
                    window_start=datetime.fromtimestamp(window_start, UTC).isoformat(),
                    count=bucket.count,
                    error_count=bucket.error_count,
                    minimum=bucket.minimum,
                    maximum=bucket.maximum,
                    total=bucket.total,
                    sketch=encode_sketch(bucket.sketch),
                )
            )
        return drained

    def drain_all(self) -> list[SketchWindow]:
        """Everything, closed or not. The last window of a run has not closed
        when JMeter stops, and losing it would drop the end of every test."""
        return self.drain(math.inf)
