# Security Policy

## Reporting a vulnerability

Report privately through
[GitHub private vulnerability reporting](https://github.com/ultron13/bonerepo/security/advisories/new),
not in a public issue.

Include what you can: affected component, version or commit, reproduction steps,
and impact. We aim to acknowledge within 3 working days and to ship a fix or a
mitigation plan within 90 days, crediting you in the advisory unless you would
rather we did not.

## Supported versions

Plimsoll uses semantic versioning. A release series is `MAJOR.MINOR`; patches
within a series are backward compatible.

| Series | Status | Security fixes until |
| --- | --- | --- |
| `main` | Pre-alpha, unreleased | While it is the development branch |
| Current minor | Supported | Superseded by the next minor, plus 90 days |
| Previous minor | Maintenance | 90 days after the next minor ships |
| Older | Unsupported | — |

In practice that means **two minor series are supported at any time**, and an
operator has at least 90 days to move between them.

Fixes land on the current minor first and are backported to the one in
maintenance as a patch release. A fix is never shipped only inside a feature
release: an operator must never have to take new behaviour to get a security
fix.

Until v1.0 a minor series may ship a breaking change, and the release notes say
so explicitly. From v1.0, breaking changes wait for a major.

### What counts as a vulnerability here

Alongside the ordinary categories, and because of what this software does:

- Anything that lets a run reach a target the [target policy](docs/architecture/05-security.md)
  does not permit, including a bypass through DNS, redirects, or a variable.
- Anything that puts a durable credential inside a generator container, which
  runs a user-supplied plan.
- Anything that lets one organisation read or write another's runs, results, or
  artifacts.
- A default that is unsafe without configuration. There is no permit-all state,
  and a deployment that has not been configured refuses rather than allows.

## Why this project needs a security policy more than most

Plimsoll's purpose is to generate as much traffic as possible against a target
system, from many machines at once, on a schedule, triggerable over an API. That
is also a working description of a distributed denial-of-service platform. The
only real difference between a load test and an attack is **authorisation**.

The project therefore treats "can this deployment be pointed at something it does
not own?" as a security question, and ships controls that are on by default.

### Target policy

Every run resolves its target hosts before any generator starts, and the run is
rejected if the target is not permitted.

- Each organisation configures an **allowlist** of hostnames, domains, and CIDR
  ranges it is authorised to test. Empty allowlist means no runs — there is no
  implicit "allow everything" state.
- Targets are resolved and checked **at run creation and again on the agent**
  before traffic starts, so a DNS record cannot be repointed after the check.
- Requests to loopback, link-local, and cloud metadata addresses
  (`169.254.169.254`) are blocked from generator containers regardless of the
  allowlist. A test plan is untrusted input, and generator containers hold
  credentials.
- Rejections and overrides are written to the audit log with the acting user.

Operators can widen the policy for a closed network — the configuration is
yours. Removing it so a public deployment can hit arbitrary hosts turns your
installation into an open attack proxy, and is not a supported configuration.

### Secrets

- Credentials are never stored in test plans. Plans reference variables
  (`${DB_PASSWORD}`), resolved at run start and injected into generator
  containers in memory.
- Git credentials, target credentials, and API keys are encrypted at rest.
- API keys are stored as hashes and shown once at creation. A leaked key is
  revoked, never recovered.
- Run artifacts and logs are scrubbed of resolved secret values before upload.

### Tenancy

Organisation isolation is enforced by PostgreSQL row-level security, not by
application-layer filters alone. A missing `WHERE` clause should fail closed.

## Scope

**In scope:** authentication and authorisation bypass, cross-tenant data access,
target-policy bypass, secret disclosure, remote code execution via test plans or
the agent protocol, container escape from a generator, and privilege escalation
through the Kubernetes runtime.

**Out of scope:** the fact that Plimsoll generates load against systems you have
authorised it to test; findings against a deployment configured to disable the
target policy; denial of service achieved by configuring Plimsoll to do exactly
what it is for.
