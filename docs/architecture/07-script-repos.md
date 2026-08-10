# 7. Script repositories

The tester-owned half of the contract. [ADR-0002](../adr/0002-git-sourced-scripts.md)
decided that test plans come from Git; this document defines what a well-formed
script repository looks like, so "point Plimsoll at your repo" has a precise
meaning for the performance engineer who owns it.

A bare repository containing nothing but a `.jmx` works — existing JMeter
assets run unchanged, and that migration story stays true. The manifest
described here is what makes a repository *self-describing*: plugins pinned,
JMeter version declared, data files and variables explicit, verifiable before
the platform ever clones it.

## Layout

Plimsoll imposes no repository structure — the plan path is configured per
script repo — but the template repository uses this shape and the documentation
assumes it:

```
your-service/
  perf/
    plimsoll.yaml        # manifest — see below
    checkout.jmx
    login.jmx
    data/
      users.csv
      cards.csv
```

Plans, data, and manifest live beside the code they exercise and version with
it. Everything a plan references resolves by **relative path** from the plan
file, because the agent clones the repository at the pinned SHA and runs from
that checkout.

## The manifest: `plimsoll.yaml`

Looked up in the plan's directory first, then upward to the repository root.
One manifest can describe many plans.

```yaml
version: 1
engine: jmeter

jmeter:
  version: "5.6.3"        # the version the plans are tested against

plugins:
  - id: jpgc-casutg       # Custom Thread Groups
    version: "2.10"
    sha256: "9f2c4a…"     # checksum of the plugin jar

plans:
  - path: checkout.jmx
    data:
      - path: data/users.csv
        distribution: unique     # shared | partitioned | unique
      - path: data/cards.csv     # distribution defaults to shared
    variables:            # names only — values come from Plimsoll secrets
      - API_BASE_URL
      - DB_PASSWORD
  - path: login.jmx
    variables:
      - API_BASE_URL
```

Rules:

- **`variables` are names, never values.** Values resolve from the platform's
  secret store at run start and are injected in memory
  ([security](05-security.md)). A manifest containing a credential fails
  verification.
- **Plugins pin a version and a checksum.** An unpinned plugin is a
  supply-chain hole and an unreproducible run.
- **`distribution` defaults to `shared`.** `partitioned` and `unique` ship
  with the v0.2 workload work — the [execution plane](02-execution-plane.md)
  defines the modes and why replicated data distorts results.
- **The manifest is optional** for a repository that needs nothing beyond stock
  JMeter: `verify` parses the plan, discovers referenced data files, and the
  run uses the pool's default generator image. It becomes required the moment a
  plan needs a plugin outside the default image — at that point inference would
  be guessing, and guessing is how a plan works locally and fails at 5,000
  virtual users.

## Plugin and JMeter-version resolution

Generator images are published per supported JMeter version
(`ghcr.io/plimsoll/generator:jmeter-5.6.3`), with the common `jpgc-*` set baked
in at pinned versions. Resolution order for a plugin named in the manifest:

1. Present in the generator image at the pinned version → use it.
2. Available from the organisation's configured plugin mirror, checksum
   verified → installed at provision time.
3. Otherwise → **verify fails**, reporting the plugin id, the version wanted,
   and which images and mirrors were checked.

**The agent never downloads plugins from the public internet at run time.**
That rule is what keeps air-gapped deployments possible and keeps a run's
execution environment reproducible from its `configuration_snapshot`. A
repository's `jmeter.version` selects the image; a pool declares which image
versions it can run.

## What `verify` checks

`POST /script-repos/{id}/verify` validates the repository against this
contract and reports every failure at once:

- Credential works, ref resolves to a commit, plan exists at that path
- Manifest parses and its schema `version` is supported
- Every declared data file exists at the ref; every file the plan references
  is declared or discoverable
- Every plugin resolves per the order above
- Declared variables cover the `${…}` references in the plan — names only;
  values are checked at run start, not here
- Risky elements are flagged (v0.2) — script samplers, process execution,
  JDBC, file access — and rejected where the organisation's element policy
  denies them ([security](05-security.md))
- The plan parses: thread groups, transaction controllers, and timers are
  reported back, which is how the workload editor knows what it is configuring

## GitHub integration

For GitHub-hosted repositories, a **GitHub App installation** is the
first-class credential (v0.2): short-lived installation tokens instead of a
long-lived PAT, fine-grained repository access, installed once per
organisation. Deploy keys and PATs remain the generic path for any Git host.

The integration works in both directions:

- **Push events** arrive at `POST /integrations/github/events` and resolve new
  script versions automatically, so a merged plan appears in Plimsoll without
  manual pinning.
- **Run verdicts return to the commit.** A completed run posts a check run on
  the pinned SHA — the SLA result lands in the pull request the tester is
  already reviewing.

## Tooling

- **Template repository** — `plimsoll/jmx-template` is the layout above with a
  working plan against the bundled demo target. `plimsoll init` scaffolds the
  same thing into an existing repository.
- **`plimsoll plan lint`** — planned alongside the v0.2 CLI: the `verify`
  checks plus static plan hygiene (GUI listeners left enabled, thread groups
  not wired to `${__P(threads)}`, absolute paths, unnamed transaction
  controllers), runnable as a pre-commit hook or CI step in the script
  repository itself, so a broken plan never merges. Packaged as a GitHub
  Action, so the check runs on every pull request to the script repository.

Large datasets remain follow-on work: Git LFS support and object-storage
references for files too big to clone per generator. Until then, keep CSV
files small enough that cloning them per generator is unremarkable.
