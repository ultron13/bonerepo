# Plimsoll v0.1 Slice 4a — Metrics and Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The run that S3 executes now reports what it measured. The agent folds JMeter samples into HDR sketches, a metrics worker merges them across generators, and the API answers percentiles computed once from the merged distribution.

**Architecture:** The agent tails `results.jtl` while JMeter writes it, folds each sample into an HDR histogram keyed by `(transaction, 5-second window)`, and publishes one small sketch per key to a Redis stream. A metrics worker consumes that stream, merges sketches that share a key, and writes one row per `(run, metric, entity, window)` into the `performance_metrics` hypertable. Percentiles are derived from the stored sketch on read — never stored as a number, never averaged.

**Tech Stack:** Python 3.12, `hdrhistogram`, Redis Streams, TimescaleDB, FastAPI, pytest.

Design: [`docs/architecture/03-metrics-pipeline.md`](../../architecture/03-metrics-pipeline.md) and [ADR-0004](../../adr/0004-hdr-histogram-metric-merging.md).
Prerequisite: [S3b](2026-08-13-v01-s3b-load-and-artifacts.md) — JMeter runs, artifacts land, runs complete.

## Global Constraints

- Everything in S3's Global Constraints continues to apply — `sa.text()` with bind parameters, `TenantSession`, a permission guard on every write, `make contracts` before any commit that changes the API surface.
- **No migration.** `performance_metrics` is already a hypertable and already under RLS, and `run_errors` already exists. If this slice appears to need a schema change, stop: it is being designed wrong.
- **Never average percentiles, and never store one as a source of truth.** The aggregation layer has no code path that accepts a pre-computed percentile from a generator. A percentile is derived from a merged sketch, on read, or it is a bug. See ADR-0004 and invariant 2.
- **`organization_id` is stamped by the worker from the run**, never read from an agent's message. An agent is outside the trust boundary; a tenant identifier it supplies is an authorisation bypass.
- **The agent ships sketches, not samples.** Bandwidth is a function of transaction count, not request count. Raw fidelity is preserved by the JTL already in object storage.
- **A metrics failure never fails a run.** Measurement is not execution: a run that generated load and lost a window is degraded reporting, not a failed test.

## File Structure

```
packages/contracts/python/plimsoll_contracts/
  metrics.py                       MetricKind, Sketch encode/decode/merge, SampleWindow
apps/agent/plimsoll_agent/
  jtl.py                           Tail results.jtl, parse rows
  aggregation.py                   Fold samples into (transaction, window) sketches
  __main__.py                      (modified) run the folder beside JMeter
apps/worker/plimsoll_worker/
  metrics.py                       Consume, merge, write
  __main__.py                      (modified) a third loop
apps/api/plimsoll_api/
  repositories/metrics.py          Read merged windows
  services/results.py              Derive percentiles from merged sketches
  routers/runs.py                  (modified) GET /runs/{id}/metrics
packages/contracts/python/plimsoll_contracts/
  results.py                       RunMetricsResponse, TransactionSummary
```

---

### Task 1: The sketch contract

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/metrics.py`, `packages/contracts/python/tests/test_metrics.py`
- Modify: `packages/contracts/python/pyproject.toml` (`hdrhistogram`), root `pyproject.toml` (testpaths), `Makefile`
- Test: `packages/contracts/python/tests/test_metrics.py`

**Interfaces:**
- Produces: `MetricKind`, `new_sketch()`, `encode_sketch()`, `decode_sketch()`, `merge_sketches()`, `percentile()`, `SketchWindow`.

- [x] **Step 1: Write the failing test**

This is the one piece where a bug is silent and expensive, so the test states the property that matters: merging then deriving is not the same as deriving then averaging, and the merged answer is the true one.

`packages/contracts/python/tests/test_metrics.py`:

```python
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
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest packages/contracts/python/tests -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_contracts.metrics`.

- [x] **Step 3: Write the contract**

Add `"hdrhistogram>=0.10"` to `packages/contracts/python/pyproject.toml`, add `"packages/contracts/python/tests"` to the root `testpaths`, and extend `make test`. Do **not** add an `__init__.py` to the new tests directory — `tests` is already a package name owned by `apps/api/tests`, and a second one breaks mypy and collection.

`packages/contracts/python/plimsoll_contracts/metrics.py`:

```python
"""Mergeable sketches, and the merge itself.

Defined in contracts because both ends depend on it being the same code: the
agent that records and the worker that merges must agree bit for bit, and the
wire format is the standard compressed HDR encoding so a future non-Python
agent can speak it too.
"""

from __future__ import annotations

import base64
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
    return sketch.encode().decode("ascii")


def decode_sketch(encoded: str) -> HdrHistogram:
    return HdrHistogram.decode(encoded.encode("ascii"))


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
    return HdrHistogram.decode(raw)


@dataclass(frozen=True)
class SketchWindow:
    """One transaction's samples over one window, from one generator."""

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
```

Note `base64` is imported for clarity of intent even though `hdrh` already
returns base64 bytes from `encode()`; drop the import if ruff flags it.

- [x] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest packages/contracts/python/tests -v`
Expected: PASS — five tests.

- [x] **Step 5: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(contracts): mergeable HDR sketches, and the merge itself"
```

---

### Task 2: The agent folds samples into windows

**Files:**
- Create: `apps/agent/plimsoll_agent/jtl.py`, `apps/agent/plimsoll_agent/aggregation.py`, `apps/agent/tests/unit/test_aggregation.py`
- Modify: `apps/agent/plimsoll_agent/__main__.py`
- Test: `apps/agent/tests/unit/test_aggregation.py`

**Interfaces:**
- Produces: `rows_from(text) -> Iterator[Sample]`, `Folder.record(sample)`, `Folder.drain(now) -> list[SketchWindow]`.

- [x] **Step 1: Write the failing test**

The folding is pure and deterministic, so it is tested without a JMeter or a socket.

`apps/agent/tests/unit/test_aggregation.py`:

```python
"""Samples in, one sketch per transaction per window out."""

from datetime import UTC, datetime

from plimsoll_agent.aggregation import Folder, Sample
from plimsoll_contracts.metrics import decode_sketch, percentile

BASE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _sample(offset: float, label: str = "Browse", elapsed: int = 100, ok: bool = True) -> Sample:
    return Sample(
        at=BASE.timestamp() + offset,
        label=label,
        elapsed=elapsed,
        success=ok,
        response_code="200" if ok else "500",
        message="" if ok else "Internal Server Error",
    )


def test_samples_in_one_window_become_one_sketch() -> None:
    folder = Folder(run_id="r", ordinal=0)
    for offset in (0.0, 1.0, 2.0, 3.0, 4.0):
        folder.record(_sample(offset))

    windows = folder.drain(BASE.timestamp() + 30)
    assert len(windows) == 1
    assert windows[0].count == 5
    assert windows[0].transaction == "Browse"


def test_a_new_window_starts_every_five_seconds() -> None:
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(1.0))
    folder.record(_sample(7.0))

    windows = folder.drain(BASE.timestamp() + 30)
    assert len(windows) == 2
    assert {w.count for w in windows} == {1}


def test_transactions_are_kept_apart() -> None:
    """Merging Browse into Checkout would make both numbers meaningless."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0, label="Browse", elapsed=100))
    folder.record(_sample(0.0, label="Checkout", elapsed=900))

    windows = {w.transaction: w for w in folder.drain(BASE.timestamp() + 30)}
    assert set(windows) == {"Browse", "Checkout"}
    assert percentile(decode_sketch(windows["Checkout"].sketch), 50) >= 890


def test_errors_are_counted_but_still_timed() -> None:
    """A failed request still took time, and hiding it flatters the result."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0, ok=True))
    folder.record(_sample(1.0, ok=False, elapsed=3000))

    window = folder.drain(BASE.timestamp() + 30)[0]
    assert window.count == 2
    assert window.error_count == 1
    assert window.maximum >= 3000


def test_an_open_window_is_not_drained_early() -> None:
    """Draining a window still being written would ship half of it and then
    ship the other half as a second window with the same key."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0))
    assert folder.drain(BASE.timestamp() + 1) == []


def test_draining_twice_does_not_repeat_a_window() -> None:
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0))
    assert len(folder.drain(BASE.timestamp() + 30)) == 1
    assert folder.drain(BASE.timestamp() + 30) == []
```

- [x] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/agent/tests/unit/test_aggregation.py -v`
Expected: FAIL — `ModuleNotFoundError: plimsoll_agent.aggregation`.

- [x] **Step 3: Write the parser and the folder**

`apps/agent/plimsoll_agent/jtl.py` reads JMeter's CSV output incrementally,
tolerating a partial final line because JMeter is still writing:

```python
"""Reading results.jtl while JMeter is still writing it."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass

# JMeter's default CSV header. Read by name rather than position: the column
# set is configurable, and a positional read would silently mis-parse.
REQUIRED = ("timeStamp", "elapsed", "label", "success")


@dataclass(frozen=True)
class Sample:
    at: float
    label: str
    elapsed: int
    success: bool
    response_code: str
    message: str


class JtlReader:
    """Holds the header and any partial trailing line between reads."""

    def __init__(self) -> None:
        self._header: list[str] | None = None
        self._remainder = ""

    def feed(self, text: str) -> Iterator[Sample]:
        buffer = self._remainder + text
        # Everything up to the last newline is complete; the rest is a line
        # JMeter has not finished writing.
        complete, _, self._remainder = buffer.rpartition("\n")
        if not complete:
            return
        for row in csv.reader(io.StringIO(complete)):
            if not row:
                continue
            if self._header is None:
                if row[0] == "timeStamp":
                    self._header = row
                continue
            record = dict(zip(self._header, row, strict=False))
            if not all(key in record for key in REQUIRED):
                continue
            try:
                yield Sample(
                    # JMeter writes epoch milliseconds.
                    at=int(record["timeStamp"]) / 1000,
                    label=record["label"],
                    elapsed=int(record["elapsed"]),
                    success=record["success"] == "true",
                    response_code=record.get("responseCode", ""),
                    message=record.get("responseMessage", ""),
                )
            except ValueError:
                # A torn row. The JTL in object storage remains authoritative.
                continue
```

`apps/agent/plimsoll_agent/aggregation.py`:

```python
"""Folding samples into one sketch per transaction per window."""

from __future__ import annotations

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
    """One per generator. Bandwidth is a function of transaction count, not
    request count, which is what keeps this flat as load grows."""

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
        bucket.minimum = sample.elapsed if bucket.count == 1 else min(bucket.minimum, sample.elapsed)
        if not sample.success:
            bucket.error_count += 1

    def drain(self, now: float) -> list[SketchWindow]:
        """Only windows that have closed. A window still being written would
        be shipped twice under the same key and merged with itself."""
        closed = [key for key in self._buckets if key[0] + WINDOW_SECONDS <= now]
        drained = []
        for key in closed:
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
```

- [x] **Step 4: Run the tests and make sure they pass**

Run: `uv run pytest apps/agent/tests/unit -v`
Expected: PASS — six new tests plus the existing lifecycle and target tests.

- [x] **Step 5: Commit**

```bash
make lint && make typecheck && make test
git add -A
git commit -s -m "feat(agent): fold samples into one sketch per transaction per window"
```

---

### Task 3: The agent ships windows while it runs

**Files:**
- Modify: `apps/agent/plimsoll_agent/__main__.py`, `apps/agent/plimsoll_agent/channel.py`, `apps/api/plimsoll_api/routers/agent.py`, `packages/contracts/python/plimsoll_contracts/agent.py`
- Test: `apps/api/tests/integration/test_metrics_ingestion.py`

**Interfaces:**
- Produces: a `metrics` frame on the agent channel; `METRICS_INGESTION` stream; the API relaying agent windows onto it.

The agent has no Redis credential and must not gain one — it reaches the
control plane over the socket it already holds, and the API publishes on its
behalf, stamping the organisation from the token. That keeps the trust boundary
where S3 put it.

- [x] **Step 1: Write the failing test**

`apps/api/tests/integration/test_metrics_ingestion.py` opens an agent socket
with a minted token, sends one `metrics` frame, and asserts a message lands on
`metrics.ingestion` carrying the run's organisation — not one the agent chose.

- [x] **Step 2: Add the frame and the relay**

In `plimsoll_contracts/agent.py`:

```python
class MetricsFrame(BaseModel):
    type: Literal["metrics"] = "metrics"
    windows: list[dict[str, str]]
```

In `messaging.py`, `METRICS_INGESTION = "metrics.ingestion"` and
`METRICS_GROUP = "metrics"`. In the agent router, on `kind == "metrics"`,
publish each window with `runId` and `organizationId` taken from `claims`,
never from the payload, then answer `Accepted`.

- [x] **Step 3: Run the folder beside JMeter**

In the agent, while `execute` runs, a task tails the JTL and drains every
`WINDOW_SECONDS`, sending what closed. On completion it drains once more with
`now = +inf` so the final partial window is not lost. A send failure is logged
and dropped: **a metrics failure never fails a run.**

- [x] **Step 4: Run the tests and commit**

```bash
make contracts && make lint && make typecheck && make test && make test-int
git commit -s -m "feat(agent): ship closed windows over the channel the agent already holds"
```

---

### Task 4: The metrics worker merges and writes

**Files:**
- Create: `apps/worker/plimsoll_worker/metrics.py`, `apps/worker/tests/unit/test_merge.py`
- Modify: `apps/worker/plimsoll_worker/__main__.py`
- Test: `apps/worker/tests/unit/test_merge.py`, `apps/api/tests/integration/test_metrics_ingestion.py`

**Interfaces:**
- Produces: `merge_batch(windows) -> list[MergedWindow]`; a third loop in the worker consuming `METRICS_INGESTION`.

The merge is keyed by `(run_id, transaction, window_start)` and **ignores
ordinal** — that is the whole point: two generators reporting the same window
produce one row whose sketch is the sum of theirs.

- [x] **Step 1: Write the failing unit test** — two generators, same window,
      one merged row whose count is the sum and whose p95 comes from the merged
      distribution rather than either input.
- [x] **Step 2: Write the merge and the writer.** One row per key with
      `metric_kind = 'histogram'`, `entity_type = 'transaction'`, the sketch in
      `sketch BYTEA`, and `organization_id` stamped from the run.
- [x] **Step 3: Add the loop** beside reconciliation and probes, on its own
      task for the same reason: a slow merge must not delay a run.
- [x] **Step 4: Run everything and commit.**

---

### Task 5: Results, read back correctly

**Files:**
- Create: `apps/api/plimsoll_api/repositories/metrics.py`, `apps/api/plimsoll_api/services/results.py`, `packages/contracts/python/plimsoll_contracts/results.py`, `apps/api/tests/integration/test_results_api.py`
- Modify: `apps/api/plimsoll_api/routers/runs.py`
- Test: `apps/api/tests/integration/test_results_api.py`

**Interfaces:**
- Produces: `GET /api/v1/runs/{id}/metrics` returning per-transaction `count`, `errorCount`, `min`, `max`, `mean`, `p50`, `p90`, `p95`, `p99`, and throughput.

- [ ] **Step 1: Write the failing test** — a completed run answers a summary
      whose percentiles are derived from merged sketches, and whose p95 for a
      transaction lies between that transaction's min and max.
- [ ] **Step 2: Write the repository** — select rows for a run, grouped by
      transaction, returning the sketches to merge. **The SQL never computes a
      percentile.**
- [ ] **Step 3: Write the service** — merge the sketches for each transaction
      once and derive every percentile from that one merged histogram.
- [ ] **Step 4: Write the endpoint**, `TEST_READ`, and regenerate contracts.
- [ ] **Step 5: Run everything and commit.**

---

## Slice acceptance

- [ ] A completed run answers `GET /runs/{id}/metrics` with per-transaction percentiles
- [ ] Percentiles are derived from merged sketches — no code path averages one, and a test demonstrates the error that would cause
- [ ] Two generators reporting the same window produce one row, not two
- [ ] `organization_id` on a metric row comes from the run, never from the agent
- [ ] The agent holds no Redis credential
- [ ] A run whose metrics fail still completes and still reports its artifacts
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`, and `make contracts` all pass, the last leaving the tree clean

## Deferred to S4b

Live streaming over `/ws/runs/{id}`, grouped errors in `run_errors`, and SLA
evaluation at completion. Each depends on merged metrics existing, and none of
them changes the ingestion path — which is why they are a second slice rather
than a longer first one.
