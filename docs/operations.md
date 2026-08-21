# Operations

Running Plimsoll, and getting it back when something goes wrong.

## Backup

```bash
make backup                 # into backups/<timestamp>
make backup DEST=/mnt/nfs/plimsoll-2026-08-20
```

Three parts, because any one of them alone is useless:

| Part | Why |
| --- | --- |
| `roles.sql` | The cluster roles. The database dump grants to `plimsoll_app`, `plimsoll_owner`, and `plimsoll_auth` 298 times and creates none of them — a restore onto a fresh cluster fails on every grant without this. |
| `plimsoll.dump` | Schema, data, row-level-security policies, and the TimescaleDB hypertable. Custom format, so a restore can be parallel and selective. |
| `objects/` | Plan bundles and run artifacts. Not in Postgres, and a run whose JTL is gone cannot be re-analysed. |

**Redis is deliberately absent.** It carries work in flight, and the reconciler
rebuilds its view from the database on the next tick. Restoring a stale queue
would replay decisions the database has already moved past.

## Restore

```bash
./scripts/restore.sh backups/20260820T190000Z --into plimsoll
```

Roles first, because every grant in the dump names one. The database next.
Objects last: an artifact nothing references is harmless, while a run that
references a missing artifact is not.

The script refuses a database that already holds runs unless `FORCE=1` is set.
A restore aimed at the wrong environment is the expensive mistake here, and it
is always made in a hurry.

## Proving the backup works

```bash
make restore-drill
```

An untested backup is not a backup. The drill takes one, restores it into a
scratch database, and compares the two on the things whose loss would matter:

```
  ok       test_runs    357
  ok       metrics      23224
  ok       audit        2916
  ok       hypertables  1
  ok       policies     24
  ok       isolation    refuses an unscoped read
```

That last line is the one to watch. A restored database that came back without
its row-level-security policies would read perfectly clean and leak across
organisations. The drill sets the runtime role and confirms an unscoped read
still returns nothing.

Run it on a schedule, not after an incident.

## What a restore does not bring back

- **Runs that were in flight.** A restored run in `RUNNING` has no containers
  behind it. The reconciler marks its generators lost once their heartbeats age
  out, and the run fails as capacity loss rather than reporting a result it
  never measured.
- **Generator containers.** They are disposable by design and are recreated per
  run. Any that survive the incident are adopted or reaped by the worker.
- **Redis streams.** See above.

## What is kept, and for how long

Nothing here grew without a bound until it was made to, and the three answers
are deliberately different.

**Metrics** are dropped after ninety days by a TimescaleDB retention policy
(`PLIMSOLL_METRICS_RETENTION` at migration time). An hour-long test at five
hundred virtual users writes a row per transaction per generator per window,
so this is the table that fills a disk. What ages out is the per-window
detail; the runs, their SLA verdicts and their merged summaries are ordinary
rows and are kept — those are the results, and what goes is the working.

Compression is **not** enabled, and not by preference: TimescaleDB refuses it
on a table with row-level security, and row-level security is the tenant
boundary. It would store these rows roughly an order of magnitude smaller, so
this is a real cost paid for a guarantee worth more than the disk.

**Sign-in sessions** are cleared by the worker, hourly. A refresh family is
written on every sign-in and its history grows once per rotation; neither is
read after the family is dead. A revoked family is kept a week, because
revocation is the theft signal and somebody investigating one wants it still
there.

**Audit logs are never deleted**, deliberately. They are a compliance record,
and an organisation that needed seven years and got ninety days finds out at
exactly the wrong moment. They are small — a few hundred megabytes a year at
enterprise volumes — and the webhook export above exists so the durable copy
can live in a system built for keeping things. If a retention rule is required,
it is a decision to make explicitly rather than a default to inherit.

## Sending events to a SIEM

An organisation subscribes to what happens here. `audit.*` is the whole trail,
which is what a SIEM wants.

```bash
curl -X POST https://plimsoll.example.com/api/v1/webhooks \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"url": "https://siem.example.com/plimsoll", "events": ["audit.*"]}'
```

The response carries a secret, once. Every delivery is signed with it:

```
x-plimsoll-signature: sha256=<hmac>
x-plimsoll-timestamp: <unix seconds>
```

The signature covers `"<timestamp>." + body`, so a receiver checking it is also
checking the age. Reject anything older than a few minutes; a signature that
does not cover the timestamp lets a captured delivery replay for as long as the
secret lives. `plimsoll_api.security.webhooks.verify` is the procedure, and is
what the tests use.

**A URL must be https and must resolve to a public address.** A webhook URL is
supplied by a tenant and fetched by the control plane, which is the shape of
every server-side request forgery there has ever been — the metadata endpoint
that hands out cloud credentials is one HTTP request away from anything that
can be made to fetch a URL. The host is resolved, every address it answers with
is checked, and the delivery connects to a checked address rather than
resolving the name a second time. A name that answers publicly when it is
checked and privately when it is used is the whole attack.

`PLIMSOLL_WEBHOOK_ALLOW_PRIVATE_ADDRESSES` permits private addresses, because
an on-premises SIEM is a real deployment. It belongs on a single-tenant
install: on a shared one it lets any organisation point a webhook at anything
the control plane can reach. Loopback and link-local stay refused either way.

**A receiver that cannot be reached is suspended**, after three attempts over
about ten seconds. Left active, a dead endpoint turns every event into a queue
of attempts that never drains, and the queue is what gets noticed rather than
the endpoint. A 4xx other than 408 or 429 is not retried at all: that is the
receiver saying it understood and declined.

## Single sign-on

An organisation configures one identity provider. Anyone with `admin.system`
can, and it is written to the audit trail either way.

```bash
curl -X PUT https://plimsoll.example.com/api/v1/identity-provider \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{
        "issuer": "https://login.example.com",
        "clientId": "plimsoll",
        "clientSecret": "...",
        "groupsClaim": "groups",
        "adminGroup": "plimsoll-admins",
        "allowedDomains": ["example.com"]
      }'
```

The response carries `startUrl` — the link to publish internally. Register
`https://<your-api>/api/v1/auth/oidc/callback` as the redirect URI at the
provider; it must match to the character.

Only an issuer is configured, because every endpoint below it can move and the
provider is the only thing that knows where they are now. Reachability is
checked before the configuration is stored, so a provider that cannot be
reached fails for the administrator setting it up rather than for the first
person trying to sign in.

**`allowedDomains` has no default and cannot be empty.** A provider that has
not said which domains it speaks for should not be able to create an account
for any address at all. An address outside the list gets no account, however
valid its token.

**An unverified email address gets no account either.** That is the route
somebody who can register at the provider would use to choose a colleague's
address and inherit their account.

**Group membership is authoritative in both directions.** Somebody added to
`adminGroup` becomes an administrator on their next sign-in; somebody removed
from it stops being one. Honouring it only in the granting direction would mean
the group controls promotion and nothing controls demotion. The last active
administrator is still protected — otherwise a group edit at the provider could
leave an organisation with nobody able to fix the configuration that caused it.

Accounts created this way have no password. `password_hash` is NULL, and
authentication refuses that, so there is no local credential to guess rather
than an unknown one.

Deactivating a user through `/api/v1/users/{id}/deactivate` also stops them
signing in through the provider. Offboarding that a second sign-in route walks
around is not offboarding.

The issuer must be `https://`. `PLIMSOLL_OIDC_ALLOW_INSECURE_ISSUER` relaxes
that and exists for the development fixture, which is a container on a private
network with no certificate. Anything on the path to a plain-HTTP provider can
rewrite its discovery document, and with it the keys every identity token is
checked against.

## Rotating the credential key

`PLIMSOLL_CREDENTIAL_KEY` encrypts the credentials table. A key that cannot be
rotated is a key that cannot be leaked safely, and until recently changing it
made every stored credential permanently unreadable.

**Deploy first.** A version that understands per-key references has to be
running before anything is re-encrypted. Rotating underneath an older build
leaves rows it refuses to open — verified the hard way while building this.

Then:

1. Put the new key in `PLIMSOLL_CREDENTIAL_KEY` and move the outgoing one into
   `PLIMSOLL_CREDENTIAL_KEYS_RETIRED`. Restart. Everything still opens: a
   retired key still decrypts what it wrote.
2. Re-encrypt:

   ```bash
   PLIMSOLL_ROTATION_DATABASE_URL=postgresql+asyncpg://postgres:...@host/plimsoll \
     uv run python scripts/rotate-credential-key.py
   ```

3. Remove the retired key and restart. Nothing references it any more.

Between steps one and three the deployment holds both keys, which is the point:
a rotation that requires an outage is a rotation that does not happen.

The script needs an operator connection rather than the application's.
Row-level security is forced on every tenant table — it applies to the table
owner too, and only a superuser is outside it — so a job that touches every
organisation's rows uses the same class of credential `pg_dump` does. Nothing
reachable from the application can read across organisations, maintenance
included.

## Health and metrics

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` (API) | Liveness |
| `GET /readyz` (API) | Readiness — Postgres, Redis, object store |
| `GET /metrics` (API, :8000) | Requests by route and outcome, latency |
| `GET /healthz` (worker, :9100) | Liveness — unhealthy when reconciliation stalls |
| `GET /metrics` (worker, :9100) | Ticks, decisions, failures, windows written |

The alert worth having is on `plimsoll_worker_last_tick_timestamp`. A worker
that has stopped reconciling looks exactly like an idle one from outside, and
that number is the only thing that distinguishes them. Restarting a stalled
worker is safe: it reads its state from the database, and a run abandoned
part-way through provisioning is adopted by whoever comes next.
