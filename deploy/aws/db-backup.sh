#!/usr/bin/env bash
# Runs ON the instance every 6 hours (systemd timer). Dumps the local database to
# s3://<bucket>/db/ and deletes dumps older than DB_BACKUP_KEEP_DAYS (30). Does nothing until the
# database runs in the container (RDS makes its own backups).
# Worst case you lose up to 6 hours of data. Prove a backup works with db-restore-test.sh.
# Look at what it did with:  journalctl -u nakama-db-backup -n 20
set -euo pipefail
export PATH=$PATH:/snap/bin
[ -f /opt/nakama/use-local-db ] || exit 0
. /opt/nakama/aws.env
KEEP_DAYS=${DB_BACKUP_KEEP_DAYS:-30}

stamp=$(date -u +%Y%m%dT%H%M%SZ)
key="db/nakama-$stamp.dump"
tmp=$(mktemp /var/tmp/nakama-dump.XXXXXX)
trap 'rm -f "$tmp"' EXIT

cd /opt/nakama/current/deploy/aws
docker compose --profile localdb exec -T db pg_dump -U nakama -d nakama -Fc --no-owner --no-acl > "$tmp"
size=$(stat -c %s "$tmp")
[ "$size" -gt 5000 ] || { echo "dump is only $size bytes; not uploading it" >&2; exit 1; }
aws s3 cp "$tmp" "s3://$S3_BUCKET/$key" --region "$AWS_REGION" --only-show-errors
echo "backed up $key ($size bytes)"

# Delete old dumps. The timestamp in the name sorts as text, so a string compare is enough.
cutoff=$(date -u -d "$KEEP_DAYS days ago" +%Y%m%dT%H%M%SZ)
aws s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix db/ --region "$AWS_REGION" \
  --query 'Contents[].Key' --output text | tr '\t' '\n' | while read -r k; do
  [ -n "$k" ] && [ "$k" != None ] || continue
  ts=${k#db/nakama-}; ts=${ts%.dump}
  if [[ "$ts" < "$cutoff" ]]; then aws s3 rm "s3://$S3_BUCKET/$k" --region "$AWS_REGION" --only-show-errors; fi
done
