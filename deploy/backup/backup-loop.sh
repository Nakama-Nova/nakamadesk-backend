#!/bin/sh
# Backs up now, then once a day. A failed run is logged and retried the next day, so
# set BACKUP_PING_URL (e.g. a healthchecks.io check) to be told when one is missed.
# ponytail: sleep loop, drifts by the run time; use cron if the hour must be fixed.
while true; do
    /scripts/backup.sh || echo "backup: FAILED at $(date -u)" >&2
    sleep 86400
done
