#!/usr/bin/env bash
# CI only:  sudo -E bash deploy/aws/ci/rollout-test.sh      (a clean Ubuntu runner with Docker)
#
# Runs the real AWS rollout end to end, with RDS faked by a plain Postgres and S3 by the `moto` emulator:
#   release on "RDS" -> rehearse the copy -> real cutover -> data survives a restart in its Docker
#   volume -> S3 dump and prune -> restore test -> photos are served from S3 and never touch disk.
# The scripts under test are the production ones; the only test knobs are RDS_SSLMODE and
# NAKAMA_SKIP_TIMERS, plus a fake `aws secretsmanager`.
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../../.." && pwd)
D=/opt/nakama/releases/ci-1
step() { printf '\n=== %s\n' "$*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }
want() { [ "$1" = "$2" ] || fail "$3 (wanted '$2', got '$1')"; }

dc() { (cd "$D/deploy/aws" && docker compose "$@"); }
dcl() { dc --profile localdb "$@"; }
sql_rds() { dc exec -T rds-sim psql -U nakama -d nakama -At -c "$1"; }
sql_local() { dcl exec -T db psql -U nakama -d nakama -At -c "$1"; }
api_url() { dc exec -T api printenv DATABASE_URL; }
healthy() { for _ in $(seq 1 30); do curl -fsS http://127.0.0.1/health/ 2>/dev/null | grep -q '"ok"' && return 0; sleep 2; done; return 1; }

on_error() { echo "--- container logs ---"; dcl logs --no-color --tail 60 2>&1 || true; }
trap on_error ERR

step "set up: fake /opt/nakama, a fake Secrets Manager, an S3 emulator"
REAL_AWS=$(command -v aws) || fail "no aws CLI on this runner"
export REAL_AWS
mkdir -p /opt/nakama/releases /opt/ci-bin
cat > /opt/ci-bin/aws <<'EOF'
#!/usr/bin/env bash
# Secrets Manager does not exist in CI: answer the two secret reads. Everything else is the real
# AWS CLI, aimed at the S3 emulator by AWS_ENDPOINT_URL_S3.
if [ "${1:-}" = secretsmanager ]; then
  case "$*" in
    *ci-db*)  printf 'ci-db-pass' ;;
    *ci-app*) printf 'ci-secret-key-0123456789-0123456789-0123456789' ;;
  esac
  exit 0
fi
exec "$REAL_AWS" "$@"
EOF
chmod +x /opt/ci-bin/aws
cat > /opt/nakama/aws.env <<'EOF'
AWS_REGION=ap-southeast-2
S3_BUCKET=nakama-ci
DB_HOST=rds-sim
DB_SECRET_ID=ci-db
APP_SECRET_ID=ci-app
SITE_ADDRESS=:80
CORS_ORIGINS=
RDS_SSLMODE=disable
EOF
cat > /opt/nakama/app.env <<'EOF'
S3_ENDPOINT_URL=http://host.docker.internal:9000
S3_ACCESS_KEY_ID=ci
S3_SECRET_ACCESS_KEY=ci
EOF
rm -rf "$D" && cp -a "$REPO" "$D"

# S3 emulator on the runner (not a container, so no image has to exist); bound to every interface
# so the API container can reach it as host.docker.internal.
python3 -m venv /opt/ci-moto
/opt/ci-moto/bin/pip install -q 'moto[server]'
nohup /opt/ci-moto/bin/moto_server -H 0.0.0.0 -p 9000 >/tmp/moto.log 2>&1 &
for _ in $(seq 1 30); do curl -fsS http://127.0.0.1:9000/moto-api/ >/dev/null 2>&1 && break; sleep 1; done
curl -fsS http://127.0.0.1:9000/moto-api/ >/dev/null || { cat /tmp/moto.log; fail "the S3 emulator did not start"; }
export PATH=/opt/ci-bin:$PATH
export AWS_ACCESS_KEY_ID=ci AWS_SECRET_ACCESS_KEY=ci AWS_DEFAULT_REGION=ap-southeast-2
export AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
export COMPOSE_FILE=docker-compose.yml:ci/docker-compose.ci.yml
export NAKAMA_SKIP_TIMERS=1

step "1. a normal release runs against RDS (the stand-in), with no local database"
bash "$D/deploy/aws/release.sh" "$D" ci-1
healthy || fail "the API does not answer through Caddy"
current=$(dc exec -T api alembic current); echo "$current" | grep -q '(head)' || fail "migrations are not at head"
case "$(api_url)" in *@rds-sim:*) ;; *) fail "the API is not using RDS: $(api_url)";; esac
[ ! -f /opt/nakama/use-local-db ] || fail "the marker exists before the cutover"
docker ps --format '{{.Names}}' | grep -qx nakama-db-1 && fail "the local db started without the cutover"
aws s3 mb s3://nakama-ci >/dev/null
echo "ok: on RDS, migrated, healthy; the local database is not running"

step "2. seed the stand-in RDS, then rehearse the copy while the API keeps running"
sql_rds "insert into categories(name) values ('ci-cat-1'),('ci-cat-2'),('ci-cat-3')"
sql_rds "insert into customer_accounts(email, name, created_at) values ('ci1@example.com','CI One',now()),('ci2@example.com','CI Two',now())"
bash "$D/deploy/aws/cutover-to-local-db.sh" --rehearse | tee /tmp/rehearse.log
grep -q "tables match" /tmp/rehearse.log || fail "the rehearsal did not compare the tables"
grep -q "rehearsal done" /tmp/rehearse.log || fail "the rehearsal did not finish"
[ ! -f /opt/nakama/use-local-db ] || fail "a rehearsal must not switch"
case "$(api_url)" in *@rds-sim:*) ;; *) fail "a rehearsal moved the API";; esac
healthy || fail "the API stopped answering during the rehearsal"
echo "ok: the copy works, nothing switched"

step "3. the real cutover"
sql_rds "insert into categories(name) values ('ci-cat-4')"   # written after the rehearsal: must be copied now
bash "$D/deploy/aws/cutover-to-local-db.sh" | tee /tmp/cutover.log
[ -f /opt/nakama/use-local-db ] || fail "the marker was not created"
case "$(api_url)" in *@db:*) ;; *) fail "the API is not on the local database: $(api_url)";; esac
healthy || fail "the API does not answer on the local database"
want "$(sql_local "select count(*) from categories")" 4 "categories copied"
want "$(sql_local "select count(*) from customer_accounts")" 2 "customer accounts copied"
echo "ok: switched, every row copied, API healthy"

step "4. the data lives in a Docker volume and survives a restart"
docker volume inspect nakama_pgdata >/dev/null || fail "no pgdata volume"
sql_local "insert into categories(name) values ('ci-persist')"
dcl down
bash "$D/deploy/aws/release.sh" "$D" ci-1
healthy || fail "the API does not answer after the restart"
want "$(sql_local "select count(*) from categories")" 5 "categories after down/up"
case "$(api_url)" in *@db:*) ;; *) fail "a release moved the API back to RDS";; esac
echo "ok: 5 rows after down and up; a release keeps the local database"

step "5. backup to S3, and old dumps are pruned"
aws s3 ls s3://nakama-ci >/dev/null 2>&1 || fail "the bucket vanished with the restart"   # the emulator is outside Docker, so it survives
head -c 6000 /dev/urandom > /tmp/old.dump
aws s3 cp /tmp/old.dump s3://nakama-ci/db/nakama-20200101T000000Z.dump --only-show-errors
bash "$D/deploy/aws/db-backup.sh"
aws s3 ls s3://nakama-ci/db/
aws s3 ls s3://nakama-ci/db/ | grep -q 20200101 && fail "the old dump was not pruned"
aws s3 ls s3://nakama-ci/db/ | grep -q '\.dump' || fail "no dump was uploaded"
echo "ok: a new dump is in S3 and the 2020 one is gone"

step "6. restore test"
bash "$D/deploy/aws/db-restore-test.sh" | tee /tmp/restore.log
grep -q "OK: restored cleanly" /tmp/restore.log || fail "the restore test did not pass"
echo "ok: the newest dump restores with the same tables"

step "7. photos are served from S3 and nothing is kept on the server's disk"
head -c 2048 /dev/urandom > /tmp/p.jpg
aws s3 cp /tmp/p.jpg s3://nakama-ci/public/items/ci/p.jpg --content-type image/jpeg --only-show-errors
aws s3 cp /tmp/p.jpg s3://nakama-ci/items/ci/private.jpg --content-type image/jpeg --only-show-errors
curl -fsS -D /tmp/h -o /tmp/got.jpg http://127.0.0.1/media/public/items/ci/p.jpg
cmp /tmp/p.jpg /tmp/got.jpg || fail "the photo served by /media differs from the one in S3"
grep -qi '^content-type: image/jpeg' /tmp/h || fail "wrong content type"
grep -qi 'immutable' /tmp/h || fail "no long cache header"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/media/items/ci/private.jpg)
want "$code" 404 "an object outside public/ must not be served"
want "$(dc exec -T api sh -c 'find /data/media -type f | wc -l' | tr -d '[:space:]')" 0 "files on the server's disk"
echo "ok: served from S3, private objects stay private, no local files"

step "ALL GOOD"
