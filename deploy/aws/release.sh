#!/usr/bin/env bash
# Runs ON the instance. Builds and starts the release already unpacked in $1, tagged $2.
# Used by deploy.sh (manual, over SSM) and pull-deploy.sh (automatic, from the production branch).
# Reads its settings from /opt/nakama/aws.env and its secrets from Secrets Manager.
set -euo pipefail
export PATH=$PATH:/snap/bin
D=$1 TAG=$2

exec 9>/var/lock/nakama-deploy.lock
flock 9 # one deploy at a time, whichever path started it

. /opt/nakama/aws.env
PREV=$(readlink /opt/nakama/current || true)

DBPW=$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$DB_SECRET_ID" --query SecretString --output text)
SK=$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$APP_SECRET_ID" --query SecretString --output text)

cd "$D/deploy/aws"
umask 077
cat > .env <<EOF
ENV=production
DATABASE_URL=postgresql://nakama:$DBPW@$DB_HOST:5432/nakama?sslmode=require
SECRET_KEY=$SK
S3_BUCKET=$S3_BUCKET
S3_REGION=$AWS_REGION
CORS_ORIGINS=$CORS_ORIGINS
SITE_ADDRESS="${SITE_ADDRESS//,/, }"
EOF
# Non-secret app settings (SMS_PROVIDER, SMS_DLT_*, ...): one KEY=value per line, edited by
# hand on the instance. Secrets stay in Secrets Manager.
if [ -f /opt/nakama/app.env ]; then cat /opt/nakama/app.env >> .env; fi

API_TAG=$TAG docker compose up -d --build

# Wait for the API to report healthy; if it never does, go back to the previous release.
for _ in $(seq 1 24); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' nakama-api-1 2>/dev/null)" = healthy ] && break
  sleep 5
done
if [ "$(docker inspect -f '{{.State.Health.Status}}' nakama-api-1 2>/dev/null)" != healthy ]; then
  echo "release $TAG did not become healthy" >&2
  if [ -n "$PREV" ] && [ -d "$PREV" ]; then
    echo "rolling back to $PREV" >&2
    (cd "$PREV/deploy/aws" && API_TAG=$(basename "$PREV") docker compose up -d)
  fi
  exit 1
fi

ln -sfn "$D" /opt/nakama/current
ls -1dt /opt/nakama/releases/* | tail -n +4 | xargs -r rm -rf
docker image prune -f >/dev/null

# Keep the auto-deployer current: it ships with each release (takes effect on its next run).
install -m 755 "$D/deploy/aws/pull-deploy.sh" /usr/local/bin/nakama-pull-deploy
install -m 644 "$D/deploy/aws/nakama-pull-deploy.service" /etc/systemd/system/
install -m 644 "$D/deploy/aws/nakama-pull-deploy.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now nakama-pull-deploy.timer >/dev/null
echo "release $TAG is live"
