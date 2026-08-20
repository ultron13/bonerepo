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
