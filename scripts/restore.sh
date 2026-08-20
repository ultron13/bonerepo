#!/usr/bin/env bash
#
# Bring a deployment back from a backup taken by scripts/backup.sh.
#
# Roles before the database, because every grant in the dump names one. The
# database before the objects, because an artifact nothing references is
# harmless while a run that references a missing artifact is not.
#
# Refuses to overwrite a database that already has runs in it unless told to.
# A restore run against the wrong environment is the expensive mistake here,
# and it is always made in a hurry.
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose -f infrastructure/docker/docker-compose.yml}"
SOURCE="${1:?usage: restore.sh <backup-directory> [--into <database>]}"
DATABASE="${3:-plimsoll}"
BUCKET="${PLIMSOLL_S3_BUCKET:-plimsoll-artifacts}"

[ -f "$SOURCE/plimsoll.dump" ] || { echo "No plimsoll.dump in $SOURCE" >&2; exit 1; }

existing=$($COMPOSE exec -T postgres psql -U postgres -d "$DATABASE" -tAc \
  "SELECT count(*) FROM test_runs" 2>/dev/null || echo 0)
if [ "${existing:-0}" -gt 0 ] && [ "${FORCE:-}" != "1" ]; then
  echo "$DATABASE already holds $existing runs. Set FORCE=1 to overwrite." >&2
  exit 1
fi

echo "Restoring $SOURCE into $DATABASE"

# Idempotent: a role that already exists is the state this wanted.
if [ -s "$SOURCE/roles.sql" ]; then
  $COMPOSE exec -T postgres psql -U postgres -v ON_ERROR_STOP=0 < "$SOURCE/roles.sql" >/dev/null 2>&1 || true
fi

$COMPOSE exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS \"$DATABASE\";" >/dev/null
$COMPOSE exec -T postgres psql -U postgres -c "CREATE DATABASE \"$DATABASE\";" >/dev/null
$COMPOSE exec -T postgres pg_restore -U postgres -d "$DATABASE" --no-owner --role=postgres < "$SOURCE/plimsoll.dump"

if [ -d "$SOURCE/objects" ]; then
  $COMPOSE cp "$SOURCE/objects/." "minio:/tmp/restore-objects/" >/dev/null
  $COMPOSE exec -T minio sh -c \
    "mc alias set restore http://127.0.0.1:9000 \
       \${MINIO_ROOT_USER} \${MINIO_ROOT_PASSWORD} >/dev/null && \
     mc mb --ignore-existing restore/$BUCKET >/dev/null && \
     mc mirror --overwrite --quiet /tmp/restore-objects restore/$BUCKET" >/dev/null
fi

echo "Restored. Verify before pointing traffic at it:"
echo "  $COMPOSE exec -T postgres psql -U postgres -d $DATABASE -c 'SELECT count(*) FROM test_runs;'"
