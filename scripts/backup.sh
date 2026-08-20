#!/usr/bin/env bash
#
# Everything needed to bring this deployment back somewhere else.
#
# Three parts, because losing any one of them makes the other two useless:
#
#   roles.sql   The cluster roles. A database dump grants to them 298 times
#               and creates none of them, so a restore onto a fresh cluster
#               fails on every grant without this.
#   plimsoll.dump  Schema, data, row-level-security policies, and the
#               TimescaleDB hypertable. Custom format, so a restore can be
#               parallel and selective.
#   objects/    Plan bundles and run artifacts. Not in Postgres, and a run
#               whose JTL is gone cannot be re-analysed.
#
# Deliberately not backed up: Redis. It carries work in flight, and the
# reconciler rebuilds its view from the database on the next tick -- restoring
# a stale queue would replay decisions the database has already moved past.
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose -f infrastructure/docker/docker-compose.yml}"
DESTINATION="${1:-backups/$(date -u +%Y%m%dT%H%M%SZ)}"
BUCKET="${PLIMSOLL_S3_BUCKET:-plimsoll-artifacts}"

mkdir -p "$DESTINATION/objects"

echo "Backing up to $DESTINATION"

# Roles first in the archive as well as in the restore: the order is the order
# they have to be applied in, and an archive that reads like the procedure is
# one less thing to get wrong under pressure.
$COMPOSE exec -T postgres pg_dumpall -U postgres --roles-only \
  | grep -E 'plimsoll_(owner|app|auth)' > "$DESTINATION/roles.sql"

# TimescaleDB's own catalog tables carry circular foreign keys and pg_dump
# says so every time. The warning is about its internal metadata, not this
# schema, and a restore of this dump has been verified to reproduce the
# hypertable intact -- see docs/operations.md.
$COMPOSE exec -T postgres pg_dump -U postgres -d plimsoll -Fc 2>/dev/null > "$DESTINATION/plimsoll.dump"

# The object store, mirrored rather than tarred: a partial transfer resumes
# instead of starting again, which matters once artifacts are measured in
# gigabytes.
$COMPOSE exec -T minio sh -c \
  "mc alias set backup http://127.0.0.1:9000 \
     \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD} >/dev/null && \
   mc mirror --overwrite --quiet backup/$BUCKET /tmp/backup-objects" >/dev/null
$COMPOSE cp "minio:/tmp/backup-objects/." "$DESTINATION/objects/" >/dev/null

cat > "$DESTINATION/MANIFEST" <<MANIFEST
taken_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
roles=roles.sql
database=plimsoll.dump
objects=objects/
bucket=$BUCKET
note=Redis is deliberately absent; the reconciler rebuilds from the database.
MANIFEST

printf 'Done. %s\n' "$(du -sh "$DESTINATION" | cut -f1)"
