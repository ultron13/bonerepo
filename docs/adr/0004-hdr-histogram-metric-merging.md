# ADR-0004 — Merge HDR histograms; never average percentiles

**Status:** Accepted · **Date:** 2026-08-10

## Context

Load is distributed across many generators, and each measures the response times
it observed. The platform must report the p95 for the run as a whole.

The tempting implementation is to have each generator report its own p95 and
combine them — by mean, or weighted by sample count. **Both are wrong.**
Percentiles are order statistics, not linear quantities. Recovering a combined
percentile requires the distributions, and a percentile is precisely the summary
that discards them.

The resulting error is not a rounding artefact. It depends on the shapes of the
underlying distributions and can be large in either direction, without any
signal that it is happening. JMeter's own Backend Listener emits per-node
percentiles, so the naive integration produces exactly this bug.

This matters more than any other measurement decision, because p95 is the number
teams put in their SLA and gate releases on.

## Decision

Generators never report percentiles. Each agent folds raw samples into an
**HDR histogram** per transaction per 5-second window and ships the mergeable
sketch. The metrics worker merges sketches across generators by adding bucket
counts, and percentiles are derived once, from the merged histogram.

Every metric declares a kind — `counter`, `gauge`, or `histogram` — with a
defined merge rule. There is no code path that accepts a pre-computed percentile
from a generator.

- Range 1 µs to 1 hour, 3 significant figures
- Standard compressed HDR wire format, base64-encoded
- Percentiles derived on read, never stored as the source of truth

## Consequences

- Reported percentiles are correct, including p99 and p99.9 where averaging is
  worst.
- Merging is associative and order-independent, so late or out-of-order
  generator data is handled without special cases.
- Bandwidth scales with transaction count, not request count — flat as load
  grows.
- Any percentile can be computed later, including ones nobody asked for at run
  time.
- Cost: sketches are bounded-precision, so a percentile is accurate to about
  0.1% of its value rather than exact. This is stated in the UI and docs. The
  trade is strongly favourable: bounded, known error instead of unbounded,
  invisible error.
- Cost: JMeter's built-in Backend Listener cannot be used as-is, because it
  emits pre-aggregated percentiles. The agent tails the raw JTL sample stream
  instead. A custom `BackendListenerClient` is a possible later optimisation,
  not a prerequisite — tailing JTL keeps the agent entirely in Python.
