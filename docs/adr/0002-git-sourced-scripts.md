# ADR-0002 — Test plans come from Git, not uploads

**Status:** Accepted · **Date:** 2026-08-10

## Context

The original design stored uploaded `.jmx` files in object storage, with a
`script_versions` row per upload holding a storage key and checksum.

That model has three problems. Versioning has to be engineered and enforced,
rather than inherited. Test plans drift away from the application code they
exercise, because they live somewhere else entirely. And a JMeter plan is rarely
one file — it references CSV data sets, `.properties`, and plugin manifests by
relative path, which a single-blob upload handles badly.

## Decision

A script is a **reference into a Git repository**: repository URL, ref, plan
path, and a credential. A script *version* is a **resolved commit SHA**.

At run creation the control plane resolves `ref → SHA` once and pins it into
`configuration_snapshot`. Generators shallow-clone at that exact SHA.

## Consequences

- Immutability is free. A commit SHA cannot change, so reproducibility needs no
  enforcement machinery, and a branch moving mid-run cannot alter what executes.
- Data files and plugin manifests resolve by relative path because they sit
  beside the plan in the repository. This is the thing the upload model handled
  worst.
- Test plans version alongside application code, with ordinary review.
- Push-triggered runs become natural rather than a bolt-on.
- Object storage drops to artifacts and reports, which is what it is good at.
- Cost: Git credential management is required from v0.1, so the `credentials`
  table and encryption move out of the hardening phase into the first release.
- Cost: the platform depends on Git host availability at run creation. Resolved
  SHAs are cached, so a host outage does not affect a run already planned.
- Users without a Git repository for their plans must create one. Judged
  acceptable — in 2026 this is where test assets belong, and an import helper
  can smooth the transition.
