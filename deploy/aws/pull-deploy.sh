#!/usr/bin/env bash
# Runs ON the instance every minute (systemd timer). Deploys the head of the `production`
# branch when it changes, but only if that commit's CI passed. The repo is public, so this
# needs no credentials: nothing in GitHub can reach the server, the server asks GitHub.
# Look at what it did with:  journalctl -u nakama-pull-deploy -n 50
set -euo pipefail
export PATH=$PATH:/snap/bin
REPO=${REPO:-Nakama-Nova/nakamadesk-backend}
BRANCH=${BRANCH:-production}
STATE=/opt/nakama

SHA=$(git ls-remote "https://github.com/$REPO.git" "refs/heads/$BRANCH" | cut -f1)
[ -n "$SHA" ] || exit 0 # branch does not exist yet
[ "$SHA" != "$(cat $STATE/deployed-sha 2>/dev/null || true)" ] || exit 0 # already live
[ "$SHA" != "$(cat $STATE/failed-sha 2>/dev/null || true)" ] || exit 0 # failed once; wait for a new commit

# Both checks must be finished and green on this exact commit.
verdict=$(curl -fsS "https://api.github.com/repos/$REPO/commits/$SHA/check-runs?per_page=100" | python3 -c '
import json, sys
runs = {r["name"]: (r["status"], r["conclusion"]) for r in json.load(sys.stdin)["check_runs"]}
need = ["test", "docker"]
states = [runs.get(n, ("missing", None)) for n in need]
if any(c in ("failure", "cancelled", "timed_out") for _, c in states): print("failed")
elif all(s == "completed" and c == "success" for s, c in states): print("ok")
else: print("waiting")
')
case "$verdict" in
  ok) ;;
  failed) echo "CI failed on $SHA; not deploying"; echo "$SHA" > $STATE/failed-sha; exit 0 ;;
  *) echo "CI not finished on $SHA yet"; exit 0 ;;
esac

TAG=$(date -u +%Y%m%d%H%M%S)-${SHA:0:7}
D=$STATE/releases/$TAG
mkdir -p "$D"
git -C "$D" init -q
git -C "$D" fetch -q --depth 1 "https://github.com/$REPO.git" "$SHA"
git -C "$D" checkout -q FETCH_HEAD

echo "deploying $SHA as $TAG"
if bash "$D/deploy/aws/release.sh" "$D" "$TAG"; then
  echo "$SHA" > $STATE/deployed-sha
  echo "deployed $SHA"
else
  echo "$SHA" > $STATE/failed-sha
  echo "deploy of $SHA failed" >&2
  exit 1
fi
