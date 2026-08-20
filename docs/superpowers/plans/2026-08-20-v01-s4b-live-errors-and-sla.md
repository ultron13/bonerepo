# Plimsoll v0.1 Slice 4b — Live Metrics, Errors, and SLA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The three v0.1 items that merged metrics unlock — a run that streams live, an error list grouped rather than repeated, and an SLA verdict per rule.

**Architecture:** The metrics worker gains an idempotent write, so a window is one row however many generators or redeliveries produced it. Each merged window is also pushed to a short Redis tail, which the API fans out over `/ws/runs/{runId}`. Errors are folded by fingerprint in the agent and upserted by the worker. SLA rules are evaluated once, at completion, against merged data.

**Tech Stack:** Python 3.12, TimescaleDB, Redis pub/sub, FastAPI WebSocket, pytest.

Design: [`docs/architecture/03-metrics-pipeline.md`](../../architecture/03-metrics-pipeline.md).
Prerequisite: [S4a](2026-08-20-v01-s4a-metrics-and-results.md) — sketches merge and results read back.

## Global Constraints

- Everything in S4a's Global Constraints continues to apply, **except** that this slice does need a migration: per-window reads require the unique key S4a recorded as its known limitation.
- **Percentiles are still never averaged and never stored.** Streaming a window means streaming its merged sketch's derived values, computed at push time from that window's merged histogram.
- **SLA evaluation reads merged data only.** A rule evaluated against one generator's view is a rule evaluated against a fraction of the load.
- **A verdict is never silently absent.** A rule whose metric produced no data is `SKIPPED` with a reason, not a pass.
- **A degraded run cannot be a clean pass.** Capacity loss is recorded on the run and carried into the outcome.

---

### Task 1: One row per window

**Files:**
- Create: `apps/api/plimsoll_api/migrations/versions/0006_metrics_unique_window.py`
- Modify: `apps/worker/plimsoll_worker/metrics.py`
- Test: `apps/worker/tests/integration/test_metrics_upsert.py`

S4a appends: two generators whose windows arrive in different reads leave two
rows for one key. The run summary is exact because it merges everything, but a
per-window read sees the window twice, and a redelivered message counts twice.

- [x] **Step 1: Write the failing test** — write the same window twice and
      assert one row, with the second write merged into the first rather than
      replacing it or adding a row.
- [x] **Step 2: The migration.** `UNIQUE (run_id, entity_id, time)` is
      permitted on a hypertable because it carries the partitioning column.
- [x] **Step 3: Merge on conflict.** Read the stored sketch, merge the incoming
      one into it, and write back within one transaction. Replacing would lose
      the other generator's samples; adding a row is what this task removes.
- [x] **Step 4: Run everything and commit.**

---

### Task 2: SLA evaluation

**Files:**
- Create: `apps/api/plimsoll_api/services/sla.py`, `apps/api/tests/unit/test_sla.py`
- Modify: `apps/worker/plimsoll_worker/__main__.py`, `packages/contracts/python/plimsoll_contracts/results.py`
- Test: `apps/api/tests/unit/test_sla.py`, `apps/api/tests/integration/test_sla_api.py`

**Interfaces:**
- Produces: `evaluate(rules, summary, degraded) -> SlaResult`; `sla_result` written on the run at completion; `GET /runs/{id}` carries it.

The evaluation is pure, which is what lets it be tested exhaustively without a
run: rules in, verdict out.

- [x] **Step 1: Write the failing test.** Cover each operator, each severity,
      the worst-verdict-wins rule, a rule naming a transaction that produced no
      data (`SKIPPED`, never `PASS`), and a degraded run.
- [x] **Step 2: Write the evaluator.**
- [x] **Step 3: Evaluate at completion** in the worker's `_finish`, against the
      merged summary S4a already computes.
- [x] **Step 4: Expose it** on the run response, regenerate contracts.
- [x] **Step 5: Run everything and commit.**

---

### Task 3: Errors, grouped

**Files:**
- Create: `apps/agent/plimsoll_agent/errors.py`, `apps/api/plimsoll_api/repositories/run_errors.py`, `apps/agent/tests/unit/test_errors.py`
- Modify: agent `__main__.py`, agent router, worker, `routers/runs.py`
- Test: `apps/agent/tests/unit/test_errors.py`, `apps/api/tests/integration/test_errors_api.py`

**Interfaces:**
- Produces: `fingerprint(code, message, transaction)`; `GET /runs/{id}/errors`.

A million failures are one problem repeated. The agent folds them by
fingerprint and ships counts with a bounded sample; the worker upserts,
summing counts and widening the first/last seen window.

- [ ] **Step 1: Write the failing test** — a fingerprint is stable across
      varying numbers and identifiers in a message, so the same fault groups
      even when its text differs.
- [ ] **Step 2: Write the fingerprint and the folder.**
- [ ] **Step 3: Ship, upsert, and expose.**
- [ ] **Step 4: Run everything and commit.**

---

### Task 4: Live streaming

**Files:**
- Create: `apps/api/plimsoll_api/routers/live.py`, `apps/api/tests/integration/test_live_stream.py`
- Modify: `apps/worker/plimsoll_worker/metrics.py`, `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_live_stream.py`

**Interfaces:**
- Produces: `WS /ws/runs/{runId}` emitting `metric`, `generator.status`, and run-terminal events.

- [ ] **Step 1: Write the failing test** — a client subscribed before a run
      starts receives windows while it is still running, and a terminal event
      when it ends.
- [ ] **Step 2: Announce each merged window** from the worker onto the run's
      live channel.
- [ ] **Step 3: Fan out** over the WebSocket, authorised with the ordinary
      access token and `TEST_READ` — this is a user's socket, not an agent's.
- [ ] **Step 4: Run everything and commit.**

---

## Slice acceptance

- [ ] A window is one row however many generators reported it or times it was delivered
- [ ] A run carries an SLA verdict per rule, and the run takes the worst
- [ ] A rule whose metric produced no data is `SKIPPED`, never a pass
- [ ] A degraded run cannot report a clean pass
- [ ] `GET /runs/{id}/errors` groups by fingerprint with counts and a bounded sample
- [ ] A client watching `/ws/runs/{id}` sees windows during the run, not only after
- [ ] `make dev`, `make lint`, `make typecheck`, `make test`, `make test-int`, and `make contracts` all pass, the last leaving the tree clean
