## What and why

<!-- What changed, and what problem it solves. Link the issue. -->

Closes #

## How it was verified

<!-- Commands run and what they showed. "Tests pass" without evidence is not verification. -->

- [ ] `make lint typecheck test`
- [ ] `make test-int` (if this touches the API, worker, or agent)
- [ ] `make dev` still starts clean

## Checklist

- [ ] Commits are signed off (`git commit -s`) — see [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Tests accompany the behaviour change
- [ ] Migrations are reversible and do not lock a large table
- [ ] No new required service in `make dev` (or discussed in an issue first)
- [ ] Documentation updated where behaviour changed

## Invariants

Confirm this change does not violate the
[invariants](../CLAUDE.md#invariants--do-not-break-these). Tick any that the
change touches, and explain in the description how it stays correct:

- [ ] Virtual users still never run in the API process
- [ ] Percentiles still come from merged histograms, never averaged
- [ ] Runs still pin immutable inputs in `configuration_snapshot`
- [ ] Queries remain organisation-scoped under RLS
- [ ] Execution commands remain idempotent
- [ ] Generator pods still never restart mid-run
- [ ] Authorisation is still enforced server-side
- [ ] Target policy is still checked before traffic starts
