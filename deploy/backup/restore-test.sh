#!/bin/sh
# Proves the newest backup can be restored. Restores it into a throwaway database
# (restore_test) on the same server, compares row counts with the live database, then
# drops it. The live database is only read. Run monthly:
#   docker compose run --rm --no-deps backup /scripts/restore-test.sh
set -eu
: "${DATABASE_URL:?}" "${BACKUP_REMOTE:?}"
scratch="${DATABASE_URL%/*}/restore_test"
dump=/tmp/restore.dump

latest="$(rclone lsf "$BACKUP_REMOTE/db" | sort | tail -n 1)"
[ -n "$latest" ] || { echo "restore-test: no backups found in $BACKUP_REMOTE/db" >&2; exit 1; }
echo "restore-test: newest backup is $latest"

cleanup() { psql "$DATABASE_URL" -qc "drop database if exists restore_test" || true; rm -f "$dump"; }
trap cleanup EXIT

rclone copyto "$BACKUP_REMOTE/db/$latest" "$dump"
psql "$DATABASE_URL" -qc "drop database if exists restore_test" -c "create database restore_test"
pg_restore --no-owner --exit-on-error --dbname="$scratch" "$dump"

echo "restore-test: migration version in the backup: $(psql "$scratch" -tAc 'select version_num from alembic_version')"
for t in users items categories sales attendance; do
    echo "restore-test: $t  backup=$(psql "$scratch" -tAc "select count(*) from $t")  live=$(psql "$DATABASE_URL" -tAc "select count(*) from $t")"
done
echo "restore-test: ok"
