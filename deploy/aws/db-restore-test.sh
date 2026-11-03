#!/usr/bin/env bash
# Runs ON the instance. Proves the newest S3 dump can be restored: restores it into a throwaway
# database next to the live one, checks it has the same tables, prints the row counts, drops it.
# A backup you have not restored is a hope, not a backup: run this after any change to backups
# and about once a month.   sudo /opt/nakama/current/deploy/aws/db-restore-test.sh
set -euo pipefail
export PATH=$PATH:/snap/bin
[ -f /opt/nakama/use-local-db ] || { echo "the database is not local yet (RDS keeps its own backups)" >&2; exit 1; }
. /opt/nakama/aws.env
cd /opt/nakama/current/deploy/aws
dc() { docker compose --profile localdb "$@"; }

key=$(aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix db/ --region "$AWS_REGION" \
  --query 'sort_by(Contents,&LastModified)[-1].Key' --output text)
[ -n "$key" ] && [ "$key" != None ] || { echo "there is no dump in s3://$S3_BUCKET/db/ yet" >&2; exit 1; }
tmp=$(mktemp /var/tmp/nakama-restore.XXXXXX)
trap 'rm -f "$tmp"; dc exec -T db psql -U nakama -d postgres -q -c "DROP DATABASE IF EXISTS nakama_restore_test WITH (FORCE)" >/dev/null 2>&1 || true' EXIT
aws s3 cp "s3://$S3_BUCKET/$key" "$tmp" --region "$AWS_REGION" --only-show-errors
echo "restoring $key ($(stat -c %s "$tmp") bytes) into a scratch database"

dc exec -T db psql -U nakama -d postgres -q -c "DROP DATABASE IF EXISTS nakama_restore_test WITH (FORCE)" -c "CREATE DATABASE nakama_restore_test"
dc exec -T db pg_restore -U nakama -d nakama_restore_test --no-owner --exit-on-error < "$tmp"

COUNTS="select table_name, (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from public.%I', table_name), false, true, '')))[1]::text from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE' order by 1"
live=$(dc exec -T db psql -U nakama -d nakama -At -F' ' -c "$COUNTS" | LC_ALL=C sort)
rest=$(dc exec -T db psql -U nakama -d nakama_restore_test -At -F' ' -c "$COUNTS" | LC_ALL=C sort)
if [ "$(echo "$live" | cut -d' ' -f1)" != "$(echo "$rest" | cut -d' ' -f1)" ]; then
  echo "FAILED: the restored database has different tables than the live one" >&2
  exit 1
fi
echo "OK: restored cleanly with the same $(echo "$rest" | wc -l) tables. Row counts (restored dump vs live now):"
awk 'NR == FNR { live[$1] = $2; next } $2 > 0 || live[$1] > 0 { printf "  %-34s %6s %6s\n", $1, $2, live[$1] }' <(echo "$live") <(echo "$rest")
echo "(live can be ahead of the dump by whatever was written since it was taken)"
