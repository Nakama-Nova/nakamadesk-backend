#!/usr/bin/env bash
# Runs ON the instance, as root (a Session Manager shell, or `aws ssm send-command`). Moves the
# database from RDS into the local `db` container.
#
#   cutover-to-local-db.sh --rehearse   copy RDS -> local and compare every table; the API keeps
#                                       running on RDS and nothing switches. Safe to run any time.
#   cutover-to-local-db.sh              stop the API, copy, compare, switch the API to the local
#                                       database, start it. A few minutes of downtime.
#
# If any step fails the API goes back to RDS, which is never modified here. After a good cutover
# RDS is left as it was (stop it, then delete it a few days later). To go back to RDS after a
# cutover, delete /opt/nakama/use-local-db and run a release: anything written since is only in
# the local database (dump it first with /usr/local/bin/nakama-db-backup).
set -euo pipefail
export PATH=$PATH:/snap/bin
REHEARSE=0
[ "${1:-}" = "--rehearse" ] && REHEARSE=1
MARK=/opt/nakama/use-local-db
[ ! -f "$MARK" ] || { echo "already using the local database"; exit 0; }

exec 9>/var/lock/nakama-deploy.lock
flock 9 # no release while the copy runs

. /opt/nakama/aws.env
CUR=$(readlink /opt/nakama/current)
TAG=$(basename "$CUR")
DBPW=$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$DB_SECRET_ID" --query SecretString --output text)
cd "$CUR/deploy/aws"
dc() { docker compose --profile localdb "$@"; }

# Exact row count of every table in the public schema, as "table count" lines.
COUNTS="select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
rds()   { dc exec -T -e PGPASSWORD="$DBPW" -e PGSSLMODE=require db psql -h "$DB_HOST" -U nakama -d nakama -At -F' ' -c "$1"; }
local_() { dc exec -T db psql -U nakama -d nakama -At -F' ' -c "$1"; }

api_back_on_rds() { # called when something fails after the API was stopped
  echo "putting the API back on RDS" >&2
  rm -f "$MARK"
  flock -u 9
  bash "$CUR/deploy/aws/release.sh" "$CUR" "$TAG" || echo "!! the API did not restart cleanly; check docker compose logs" >&2
}

if [ "$REHEARSE" = 0 ]; then
  echo "1/5 stopping the API so nothing is written during the copy"
  docker compose stop api
fi

echo "2/5 starting the local database"
mkdir -p /srv/nakama/pgdata
dc up -d db
for _ in $(seq 1 30); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' nakama-db-1 2>/dev/null)" = healthy ] && break
  sleep 3
done
[ "$(docker inspect -f '{{.State.Health.Status}}' nakama-db-1 2>/dev/null)" = healthy ] || {
  echo "the local database did not start" >&2; [ "$REHEARSE" = 1 ] || api_back_on_rds; exit 1; }
# A clean copy every time (a rehearsal may have left data behind).
dc exec -T db psql -U nakama -d postgres -q -c "DROP DATABASE IF EXISTS nakama WITH (FORCE)" -c "CREATE DATABASE nakama"

echo "3/5 copying RDS -> local"
if ! dc exec -T -e PGPASSWORD="$DBPW" -e PGSSLMODE=require db pg_dump -h "$DB_HOST" -U nakama -d nakama -Fc --no-owner --no-acl \
     | dc exec -T db pg_restore -U nakama -d nakama --no-owner --exit-on-error; then
  echo "the copy failed" >&2; [ "$REHEARSE" = 1 ] || api_back_on_rds; exit 1
fi

echo "4/5 comparing every table"
a=$(rds "$COUNTS" | LC_ALL=C sort); b=$(local_ "$COUNTS" | LC_ALL=C sort) # same order on both sides
if [ "$a" != "$b" ]; then
  echo "ROW COUNTS DIFFER (RDS on the left, local on the right):" >&2
  diff <(echo "$a") <(echo "$b") >&2 || true
  [ "$REHEARSE" = 1 ] && echo "(in a rehearsal the API is still writing, so small differences can be normal)" >&2
  [ "$REHEARSE" = 1 ] || api_back_on_rds
  exit 1
fi
echo "   all $(echo "$a" | wc -l) tables match. Tables with rows:"
echo "$b" | awk '$2 > 0 { printf "     %-34s %s\n", $1, $2 }'

if [ "$REHEARSE" = 1 ]; then
  echo "rehearsal done: the copy works. The API is still on RDS. Run without --rehearse to switch."
  exit 0
fi

echo "5/5 switching the API to the local database"
touch "$MARK"
flock -u 9
if ! bash "$CUR/deploy/aws/release.sh" "$CUR" "$TAG"; then
  echo "the API did not become healthy on the local database" >&2
  rm -f "$MARK"
  bash "$CUR/deploy/aws/release.sh" "$CUR" "$TAG" || true
  exit 1
fi
echo "done: the API now uses the local database. RDS is untouched; stop it once you are happy:"
echo "  aws rds stop-db-instance --db-instance-identifier ${DB_HOST%%.*}"
