# Backup sidecar: pg_dump 16 (matches the db image) + rclone to any S3-style store (R2).
FROM postgres:16-alpine
RUN apk add --no-cache rclone
COPY backup.sh backup-loop.sh restore-test.sh /scripts/
RUN chmod +x /scripts/*.sh
ENTRYPOINT []
CMD ["/scripts/backup-loop.sh"]
