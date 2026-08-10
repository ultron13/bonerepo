# Contributing to Plimsoll

Thanks for considering it. Plimsoll is pre-alpha, which means design feedback is
worth as much as code right now — if an [ADR](docs/adr/) looks wrong to you,
open an issue and say so.

## Ground rules

- Be civil. The [Code of Conduct](CODE_OF_CONDUCT.md) applies everywhere,
  including pull-request review.
- Discuss substantial changes in an issue before writing them. A rejected large
  pull request wastes your time, and we would rather not.
- Small, focused pull requests get reviewed quickly. Large mixed ones do not.

## Developer Certificate of Origin

Plimsoll uses the [DCO](https://developercertificate.org/) rather than a
contributor licence agreement. You keep copyright in your contribution; you
certify you have the right to submit it under Apache-2.0.

Sign off every commit:

```bash
git commit -s -m "Add histogram merge for transaction percentiles"
```

That appends a `Signed-off-by:` trailer. CI rejects unsigned commits. Fix an
existing branch with `git rebase --signoff main`.

## Getting set up

You need **Docker** and **make**. Nothing else — if you find you need something
else, that is a bug in the setup and worth an issue.

```bash
git clone https://github.com/plimsoll/plimsoll.git
cd plimsoll
make dev        # starts 6 containers, migrates, seeds a demo project
make test       # unit tests
```

`make dev` starting cleanly on a machine with only Docker is a hard requirement.
Do not merge changes that break it.

For work touching the Kubernetes runtime, `make dev-k8s` spins up a `kind`
cluster. CI runs that path so the production runtime stays exercised; you do not
need it locally for most changes.

## Development workflow

1. Branch from `main`.
2. Write a failing test first where the change is testable.
3. Implement, keeping the [invariants in CLAUDE.md](CLAUDE.md#invariants--do-not-break-these) intact.
4. Run `make lint typecheck test` before pushing.
5. Open a pull request describing *what changed and why*. Link the issue.

### Before you push

| Check | Command |
| --- | --- |
| Formatting and lint | `make lint` |
| Types | `make typecheck` |
| Unit tests | `make test` |
| Integration tests, if you touched the API, worker, or agent | `make test-int` |
| Migrations apply and roll back cleanly | `make migrate` |

## What we look for in review

- **Correctness of the invariants first.** Averaging percentiles, unscoped
  queries, or work running in the API process will be sent back regardless of
  how clean the code is.
- Tests that would fail without the change.
- Migrations that are reversible and do not lock a large table.
- No new user-facing strings buried in business logic.
- No new required service in `make dev` without a discussion first — the
  six-container budget is deliberate and protects contributor onboarding.

## Architecture decisions

Anything that changes a decision in `docs/adr/` needs a new ADR superseding the
old one, in the same pull request. Copy the format of an existing record. ADRs
are cheap to write and save arguments later.

## Reporting bugs

Use the issue templates. A load-testing platform is full of timing-dependent
behaviour, so please include the run ID, the generator runtime (Docker or
Kubernetes), generator count, and the relevant portion of the run's artifacts.

**Do not report security vulnerabilities in a public issue.** See
[SECURITY.md](SECURITY.md).

## Areas that need help

Pre-alpha, so nearly everything. The pieces where outside experience is most
valuable:

- **JMeter internals** — JTL parsing edge cases, plugin handling, and correctly
  driving `jpgc-casutg` thread groups for step and spike profiles.
- **Kubernetes** — quota, scheduling, and clean teardown of failed runs.
- **Statistics** — histogram merging, and honest treatment of the fact that
  bounded-precision sketches trade a little accuracy for mergeability.
- **Accessibility** — the spec targets WCAG 2.1 AA, and charts are the hard part.
