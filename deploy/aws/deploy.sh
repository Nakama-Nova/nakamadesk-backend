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
# Optional, and remembered on the instance for later deploys:
#   SITE_ADDRESS=api.example.com,api2.example.com   serve these hostnames (comma, no spaces)
#                                   instead of the sslip.io one
#   CORS_ORIGINS=https://a.com,https://b.com   browser origins allowed to call the API
SITE=${SITE_ADDRESS:-}; CORS=${CORS_ORIGINS:-}
[ -n "$SITE" ] && URL="https://${SITE%%,*}"  # the first hostname answers the health check
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
set_kv() { if grep -q "^\$1=" /opt/nakama/aws.env; then sed -i "s|^\$1=.*|\$1=\$2|" /opt/nakama/aws.env; else echo "\$1=\$2" >> /opt/nakama/aws.env; fi; }
# Overrides are saved on the instance, so a later plain deploy keeps them.
if [ -n "$SITE" ]; then set_kv SITE_ADDRESS "$SITE"; fi
if [ -n "$CORS" ]; then set_kv CORS_ORIGINS "$CORS"; fi
. /opt/nakama/aws.env
D=/opt/nakama/releases/$TAG
mkdir -p \$D && aws s3 cp s3://\$S3_BUCKET/deploy/$TAG.tgz - --region \$AWS_REGION | tar -xz -C \$D
bash \$D/deploy/aws/release.sh \$D $TAG
echo "HOST=\${SITE_ADDRESS%%,*}"  # the first hostname answers the health check below
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
RESULT=$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' --output text)
printf '%s\n' "$RESULT" | tail -n 30
case "$RESULT" in Success*) ;; *) echo "Release failed on the instance (rolled back if there was a previous one)" >&2; exit 1 ;; esac
HOST=$(printf '%s\n' "$RESULT" | sed -n 's/^HOST=//p' | tail -n 1)
[ -n "$HOST" ] && URL="https://$HOST"

for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "$URL/health/" | grep -q '"ok"'; then echo "Up: $URL/health/"; exit 0; fi
  echo "Not up yet ($i)"; sleep 15
done
echo "Health check failed: $URL/health/" >&2; exit 1
