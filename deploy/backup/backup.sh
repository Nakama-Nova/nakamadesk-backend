#!/bin/sh
# One backup: a custom-format pg_dump plus a copy of the media folder, to $BACKUP_REMOTE.
# Needs DATABASE_URL and BACKUP_REMOTE (an rclone path such as backup:nakama-backups).
set -eu
: "${DATABASE_URL:?}" "${BACKUP_REMOTE:?}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"

name="nakama-$(date -u +%Y%m%dT%H%M%SZ).dump"
tmp="/tmp/$name"
trap 'rm -f "$tmp"' EXIT

pg_dump --format=custom --no-owner --file="$tmp" "$DATABASE_URL"
[ -s "$tmp" ] || { echo "backup: dump is empty" >&2; exit 1; }
rclone copyto "$tmp" "$BACKUP_REMOTE/db/$name"

# Photos and 3D files that live on local disk. `copy` never deletes from the remote.
if [ -d /media ]; then
    rclone copy /media "$BACKUP_REMOTE/media"
fi

# Keep the last $KEEP_DAYS days of database dumps.
rclone delete "$BACKUP_REMOTE/db" --min-age "${KEEP_DAYS}d"

echo "backup: ok $name ($(wc -c <"$tmp") bytes)"
if [ -n "${BACKUP_PING_URL:-}" ]; then
    wget -q -O /dev/null "$BACKUP_PING_URL" || echo "backup: ping failed" >&2
fi
