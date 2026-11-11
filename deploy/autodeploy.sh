#!/usr/bin/env bash
# Pull-based continuous deployment, run on the server by a systemd timer (calib-deploy.timer).
#
# Redeploys only when origin/main has a new commit AND every GitHub check on that commit passed.
# The server pulls instead of CI pushing, so SSH can stay restricted to the admin's IP and no
# deploy key lives in GitHub.
set -euo pipefail

cd /srv/calib
[[ -f deploy/.env ]] || { echo "deploy/.env missing; run deploy/deploy.sh once from your machine"; exit 0; }

git fetch -q origin main
target="$(git rev-parse origin/main)"
[[ "$target" == "$(git rev-parse HEAD)" ]] && exit 0

repo="$(git remote get-url origin | sed -E 's#(\.git)?$##; s#^.*github\.com[:/]##')"
checks="$(curl -fsS -H 'Accept: application/vnd.github+json' \
  "https://api.github.com/repos/$repo/commits/$target/check-runs?per_page=100")"
total="$(jq '.total_count' <<<"$checks")"
pending="$(jq '[.check_runs[] | select(.status != "completed")] | length' <<<"$checks")"
failed="$(jq '[.check_runs[] | select(.status == "completed" and .conclusion != "success" and .conclusion != "skipped")] | length' <<<"$checks")"

if (( total == 0 || pending > 0 )); then
  echo "CI not finished for ${target:0:7}; will retry next tick"
  exit 0
fi
if (( failed > 0 )); then
  echo "CI failed for ${target:0:7}; not deploying"
  exit 0
fi

echo "Deploying ${target:0:7}"
git reset -q --hard "$target"
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build --remove-orphans
docker image prune -f >/dev/null
