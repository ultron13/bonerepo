# ADR-0006 — Orchestrate Apache JMeter; do not build a virtual-user engine

**Status:** Accepted · **Date:** 2026-08-10

## Context

The original plan made a bespoke HTTP executor the first thing built, with
JMeter, k6, and Gatling as later plugins.

That inverts the project's own thesis. Its stated value is *centralised
orchestration*, yet the plan spent the first months rebuilding the most
commoditised component in the space — competing directly with k6, Gatling,
JMeter, and Locust, all mature and well-funded — while the actual differentiator
was built last. It also meant nothing was demonstrable until very late, which is
the usual way an open-source project dies quietly.

## Decision

Plimsoll does not implement a virtual-user engine. **Apache JMeter is the first
and only executor at v0.1**, driven headless per generator:

```
jmeter -n -t <plan>.jmx -l results.jtl -Jthreads=<n> -Jrampup=<s> -Jduration=<s>
```

Workload parameters are passed as JMeter properties. The plan file is never
rewritten.

JMeter over k6 for three reasons: it is Apache-2.0, matching this project's
licence and avoiding the packaging constraints k6's AGPL-3.0 would impose; the
teams this platform targets already have `.jmx` assets; and its concepts map
cleanly onto the domain model — Transaction Controllers to transactions, Timers
to think time, Constant Throughput Timer to pacing, CSV Data Set Config to
parameterisation.

The `Executor` interface still exists from day one, but is validated with one
real implementation rather than designed against a hypothetical second.

## Consequences

- Every protocol JMeter supports is inherited for free.
- Effort concentrates on orchestration, measurement, analysis, and reporting —
  where the gap actually is.
- A working demo arrives in weeks rather than months.
- Existing JMeter plans run unchanged, which is a real migration story.
- Cost: **thread-per-VU changes the capacity model.** JMeter allocates an OS
  thread per virtual user, so a realistic ceiling is roughly 500–2,000 VUs per
  generator, not the 5,000 a goroutine-based engine sustains. A 50,000-VU test
  implies 25–50 generators. `max_vus_per_generator` is therefore declared per
  pool, never assumed globally.
- Cost: **ramp profiles are limited.** Stock Thread Group ramp-up is linear only.
  Step, spike, and custom profiles need the `jpgc-casutg` plugin, and are
  documented as plugin-dependent rather than presented as universal.
- Cost: a JVM in every generator image — a larger image and higher memory
  floor than a static binary.
- Cost: dependence on JTL output format across JMeter versions. Supported
  versions are pinned and tested.
- The interface risks being JMeter-shaped until a second executor exists. A
  second engine in v0.4 is scheduled specifically to flush that out.
