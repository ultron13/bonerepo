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

Pre-alpha: only `main` is supported. A support policy for released versions
arrives with v0.1.

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
