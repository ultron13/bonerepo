# ADR-0007 — The target policy rejects by default

**Status:** Accepted · **Date:** 2026-08-10

## Context

Plimsoll generates as much traffic as it can against a target system, from many
machines at once, on a schedule, triggerable over an API. That is also a working
description of a distributed denial-of-service platform. The only property
separating a load test from an attack is **authorisation to hit the target**.

The target policy was introduced when the specification was reworked for open
source ([design record](../superpowers/specs/2026-08-10-plimsoll-oss-design.md)),
and its posture was chosen by the author rather than decided by the maintainer —
shipping without any such control would have been unsafe, so a default was
picked and explicitly flagged for review. This record closes that item.

The question left open was whether the policy should **reject** an
unauthorised target or merely **warn and audit** it.

## Decision

The policy rejects. Concretely:

- Each organisation configures an allowlist of hostnames, domain suffixes, and
  CIDR ranges. **An empty allowlist permits no runs.** There is no implicit
  permit-all state and no first-run convenience exception.
- Targets are checked at run creation and again on the agent immediately before
  traffic starts, so a DNS record repointed between admission and execution
  cannot slip through.
- Loopback, link-local, and cloud metadata endpoints are blocked from generator
  containers regardless of the allowlist.
- The policy version in force is snapshotted onto the run, so an audit can
  reconstruct what was permitted at the time.
- Operators may widen the policy — a single broad CIDR is a legitimate
  configuration for a closed network. Disabling it on an internet-reachable
  deployment turns the installation into an open attack proxy and is not a
  supported configuration.

**Rejected: warn-and-audit.** A soft posture turns the one control separating a
load test from an attack into advice. Its audit trail records the attack rather
than preventing it, and the record arrives after the traffic. The failure mode
of rejecting is an inconvenienced engineer who adds an allowlist entry; the
failure mode of warning is a third party absorbing 5,000 virtual users. Those
costs are not comparable.

**Rejected: a permit-all state for first-run convenience.** The onboarding
problem it solves is real but is solved better by shipping a target: `make dev`
seeds an allowlist entry for the bundled `demo-target` container, so the first
run works because it has a permitted system under test, not because the check
is soft.

## Consequences

- No deployment can generate traffic before someone states, in configuration,
  what it is allowed to hit. That statement is the record of authorisation.
- Every new organisation hits one deliberate speed bump before its first run.
  Preflight reports it as an actionable failure naming the rejected host, not a
  generic error.
- The double check costs a DNS resolution on the agent immediately before
  traffic. Cheap, and the only defence against a target repointed after
  admission.
- Operators of closed networks configure one broad range once. The control
  stays present and auditable rather than being switched off.
- Rejections and policy overrides are audited with the acting user, which is
  what makes the control defensible after the fact.
- Cost: a legitimate test against a newly provisioned host fails until the
  allowlist catches up. Accepted — that is the control working, and the
  alternative is a platform whose safety depends on everyone reading the
  warnings.
- Per-project allowlists layered under the organisation policy are a roadmap
  item; they narrow this control, never widen it.
