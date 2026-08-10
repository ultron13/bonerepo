# ADR-0001 — Modular monolith, not microservices

**Status:** Accepted · **Date:** 2026-08-10

## Context

The platform has roughly two dozen functional modules. The obvious enterprise
instinct is a service per module. With a small team and no users yet, that buys
distributed-systems overhead — service discovery, network failure between every
pair of modules, distributed transactions, twenty deployment pipelines — before
there is any scaling pressure to justify it.

The genuine scaling requirement is narrower and specific: **a long-running test
must never block the control plane**.

## Decision

One API application, one worker application, one agent. Modules keep real
internal boundaries: `api → service → repository → model`, with cross-module
calls going through service interfaces rather than reaching into another
module's repositories or tables.

The execution workers deploy and scale independently of the API, because that is
the boundary where the real pressure exists.

## Consequences

- One codebase to run, test, and debug. `make dev` stays six containers.
- Refactoring across module boundaries stays cheap while the domain model is
  still moving.
- Transactions remain local, so there is no saga machinery to maintain.
- Extraction later is a mechanical exercise, because the seams already exist.
- The discipline is not free: without review pressure, module boundaries erode
  into a genuine monolith. Imports that cross a boundary without going through
  a service interface are a review failure.
- The API and worker scale together even where only one needs it. Acceptable at
  this size.
