#!/usr/bin/env bash
#
# Prove the backup can be restored, without touching the live database.
#
# An untested backup is not a backup. This takes one, restores it into a
# scratch database, and compares the two on the things whose loss would matter:
# the rows, the hypertable, and the row-level-security policies. A restored
# database that came back without its policies would read clean and leak across
# organisations.
#
# Two of these are here because they are the things a dump does not obviously
# carry. TimescaleDB's retention policy lives in a background-job catalogue
# rather than in the schema, so pg_dump does not write it out and a restored
# database would start growing again with nobody told. The SECURITY DEFINER
# functions are what login and the session purge go through; a restore missing
# them reads clean and cannot authenticate anybody.
set -euo pipefail

COMPOSE="${COMPOSE:-docker compose -f infrastructure/docker/docker-compose.yml}"
WORKSPACE="$(mktemp -d)"
SCRATCH="restore_drill_$(date -u +%s)"
trap 'rm -rf "$WORKSPACE"; $COMPOSE exec -T postgres psql -U postgres -c "DROP DATABASE IF EXISTS \"$SCRATCH\";" >/dev/null 2>&1 || true' EXIT

./scripts/backup.sh "$WORKSPACE/backup" >/dev/null
./scripts/restore.sh "$WORKSPACE/backup" --into "$SCRATCH" >/dev/null

query() {
  $COMPOSE exec -T postgres psql -U postgres -d "$1" -tAc "$2" 2>/dev/null || echo "ERROR"
}

failures=0
for check in \
  "test_runs:SELECT count(*) FROM test_runs" \
  "metrics:SELECT count(*) FROM performance_metrics" \
  "audit:SELECT count(*) FROM audit_logs" \
  "hypertables:SELECT count(*) FROM timescaledb_information.hypertables" \
  "policies:SELECT count(*) FROM pg_policies" \
  "retention:SELECT count(*) FROM timescaledb_information.jobs WHERE proc_name = 'policy_retention'" \
  "authfns:SELECT count(*) FROM pg_proc WHERE proname LIKE 'auth\\_%' OR proname LIKE 'maintenance\\_%'"
do
  name="${check%%:*}"
  sql="${check#*:}"
  live="$(query plimsoll "$sql")"
  copy="$(query "$SCRATCH" "$sql")"
  if [ "$live" = "$copy" ]; then
    printf '  ok       %-12s %s\n' "$name" "$copy"
  else
    printf '  MISMATCH %-12s live=%s restored=%s\n' "$name" "$live" "$copy"
    failures=$((failures + 1))
  fi
done

# The check that matters most: a restored database still refuses an unscoped
# read. One that answered would have lost its isolation without saying so.
# tail -1: SET ROLE prints its own acknowledgement before the answer.
leaked="$(query "$SCRATCH" "SET ROLE plimsoll_app; SELECT count(*) FROM test_runs" | tail -1)"
if [ "$leaked" = "0" ]; then
  printf '  ok       %-12s refuses an unscoped read\n' "isolation"
else
  printf '  MISMATCH %-12s an unscoped read returned %s rows\n' "isolation" "$leaked"
  failures=$((failures + 1))
fi

[ "$failures" -eq 0 ] || { echo "Restore drill failed with $failures mismatch(es)." >&2; exit 1; }
echo "Restore drill passed."
