#!/usr/bin/env bash
# Ship the current working tree to the API instance and restart it.
#   AWS_PROFILE=default deploy/aws/deploy.sh [stack-name]
# Needs the AWS CLI and git locally; nothing is installed on the instance by hand.
set -euo pipefail

STACK=${1:-nakama}
export AWS_REGION=${AWS_REGION:-ap-southeast-2} AWS_DEFAULT_REGION=${AWS_REGION:-ap-southeast-2}
out() { aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }

winpath() { cygpath -m "$1" 2>/dev/null || echo "$1"; } # aws.exe on Git Bash needs Windows paths
BUCKET=$(out Bucket); INSTANCE=$(out InstanceId); URL=$(out ApiUrl)
# Optional: SITE_ADDRESS=api2.example.com deploy.sh  serves that hostname instead of the sslip.io one.
SITE=${SITE_ADDRESS:-}; [ -n "$SITE" ] && URL="https://$SITE"
TAG=$(date -u +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)
TARBALL=$(mktemp -d)/release.tgz

# Tracked + untracked-but-not-ignored files, so uncommitted work ships too.
git ls-files -z -co --exclude-standard | tar --null -T - -czf "$TARBALL"
aws s3 cp "$(winpath "$TARBALL")" "s3://$BUCKET/deploy/$TAG.tgz" --only-show-errors

# Runs on the instance via SSM. Secrets are read there from Secrets Manager.
read -r -d '' REMOTE <<EOF || true
set -euo pipefail
export PATH=\$PATH:/snap/bin
cloud-init status --wait >/dev/null 2>&1 || true  # first boot may still be installing Docker
. /opt/nakama/aws.env
if [ -n "$SITE" ]; then SITE_ADDRESS="$SITE"; fi
D=/opt/nakama/releases/$TAG
mkdir -p \$D && aws s3 cp s3://\$S3_BUCKET/deploy/$TAG.tgz - --region \$AWS_REGION | tar -xz -C \$D
DBPW=\$(aws secretsmanager get-secret-value --region \$AWS_REGION --secret-id \$DB_SECRET_ID --query SecretString --output text)
SK=\$(aws secretsmanager get-secret-value --region \$AWS_REGION --secret-id \$APP_SECRET_ID --query SecretString --output text)
cd \$D/deploy/aws
umask 077
cat > .env <<ENVEOF
ENV=production
DATABASE_URL=postgresql://nakama:\$DBPW@\$DB_HOST:5432/nakama?sslmode=require
SECRET_KEY=\$SK
S3_BUCKET=\$S3_BUCKET
S3_REGION=\$AWS_REGION
CORS_ORIGINS=\$CORS_ORIGINS
SITE_ADDRESS=\$SITE_ADDRESS
ENVEOF
API_TAG=$TAG docker compose up -d --build
ln -sfn \$D /opt/nakama/current
ls -1dt /opt/nakama/releases/* | tail -n +4 | xargs -r rm -rf
docker image prune -f >/dev/null
EOF

# SSM runs commands with sh (dash on Ubuntu); hand the script to bash.
SCRIPT="bash <<'REMOTE_EOF'"$'\n'"$REMOTE"$'\n'"REMOTE_EOF"
PARAMS=$(mktemp)
printf '%s' "$SCRIPT" | python -c 'import json,sys;print(json.dumps({"commands":[sys.stdin.read()]}))' > "$PARAMS"
CMD_ID=$(aws ssm send-command --instance-ids "$INSTANCE" --document-name AWS-RunShellScript \
  --timeout-seconds 900 --parameters "file://$(winpath "$PARAMS")" \
  --query Command.CommandId --output text)
echo "Deploying $TAG (SSM command $CMD_ID)..."
aws ssm wait command-executed --command-id "$CMD_ID" --instance-id "$INSTANCE" || true
aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE" \
  --query '[Status,StandardErrorContent]' --output text | tail -n 30

for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "$URL/health/" | grep -q '"ok"'; then echo "Up: $URL/health/"; exit 0; fi
  echo "Not up yet ($i)"; sleep 15
done
echo "Health check failed: $URL/health/" >&2; exit 1
