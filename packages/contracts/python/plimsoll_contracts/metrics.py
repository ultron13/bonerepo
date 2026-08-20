"""Mergeable sketches, and the merge itself.

Defined in contracts because both ends depend on it being the same code: the
agent that records and the worker that merges must agree bit for bit, and the
wire format is the standard compressed HDR encoding so a future non-Python
agent can speak it too.

ADR-0004 is the reason this module exists rather than a `p95` column. A
percentile is an order statistic: no arithmetic on summarised percentiles
recovers the true value, and the error is unbounded in an unpredictable
direction. Sketches merge; percentiles do not.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hdrh.histogram import HdrHistogram

# 1 microsecond to one hour, three significant figures. The range has to cover
# a pathological timeout without losing resolution on a fast response.
LOWEST_VALUE = 1
HIGHEST_VALUE = 3_600_000_000
SIGNIFICANT_FIGURES = 3

# The base window every agent emits at. The worker and the aggregates roll up
# from here; nothing downstream may assume a finer one exists.
WINDOW_SECONDS = 5


class MetricKind(StrEnum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


def new_sketch() -> HdrHistogram:
    return HdrHistogram(LOWEST_VALUE, HIGHEST_VALUE, SIGNIFICANT_FIGURES)


def encode_sketch(sketch: HdrHistogram) -> str:
    """Base64 of the standard compressed HDR encoding, safe in JSON."""
    encoded: bytes = sketch.encode()
    return encoded.decode("ascii")


def decode_sketch(encoded: str) -> HdrHistogram:
    decoded: HdrHistogram = HdrHistogram.decode(encoded.encode("ascii"))
    return decoded


def merge_sketches(encoded: list[str]) -> HdrHistogram:
    """Add bucket counts. Associative and order-independent, which is what lets
    generators report in whatever order they finish."""
    merged = new_sketch()
    for item in encoded:
        merged.add(decode_sketch(item))
    return merged


def percentile(sketch: HdrHistogram, value: float) -> int:
    """Derived, never stored. A percentile that was never asked for at run time
    can still be answered later, because the distribution was kept."""
    return int(sketch.get_value_at_percentile(value))


def to_bytes(sketch: HdrHistogram) -> bytes:
    """For the `sketch BYTEA` column."""
    return bytes(sketch.encode())


def from_bytes(raw: bytes) -> HdrHistogram:
    decoded: HdrHistogram = HdrHistogram.decode(bytes(raw))
    return decoded


@dataclass(frozen=True)
class SketchWindow:
    """One transaction's samples over one window, from one generator.

    Every field is a string on the wire because a Redis stream entry is a flat
    field map; the types are restored on the way in.
    """

    run_id: str
    ordinal: int
    transaction: str
    window_start: str
    count: int
    error_count: int
    minimum: int
    maximum: int
    total: int
    sketch: str

    def as_message(self) -> dict[str, str]:
        return {
            "runId": self.run_id,
            "ordinal": str(self.ordinal),
            "transaction": self.transaction,
            "windowStart": self.window_start,
            "count": str(self.count),
            "errorCount": str(self.error_count),
            "min": str(self.minimum),
            "max": str(self.maximum),
            "total": str(self.total),
            "sketch": self.sketch,
        }

    @classmethod
    def from_message(cls, payload: dict[str, str]) -> SketchWindow:
        return cls(
            run_id=payload["runId"],
            ordinal=int(payload["ordinal"]),
            transaction=payload["transaction"],
            window_start=payload["windowStart"],
            count=int(payload["count"]),
            error_count=int(payload["errorCount"]),
            minimum=int(payload["min"]),
            maximum=int(payload["max"]),
            total=int(payload["total"]),
            sketch=payload["sketch"],
        )
