#!/usr/bin/env bash
# Deploy (or redeploy) main to the server: pull, write the server's .env, rebuild, restart.
# Usage: deploy/deploy.sh            (reads deploy/deploy.env)
# CI calls it with DEPLOY_HOST / APP_HOST / APP_PATH already in the environment.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
if [[ -f "$here/deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "$here/deploy.env"
fi
: "${DEPLOY_HOST:?set DEPLOY_HOST (see deploy/deploy.env.example)}"
: "${APP_HOST:?set APP_HOST}"

# The secret path: generated once, then reused so the link in your cover letter keeps working.
if [[ -z "${APP_PATH:-}" ]]; then
  APP_PATH="$(openssl rand -hex 12)"
  if [[ -f "$here/deploy.env" ]]; then
    sed -i.bak "s/^APP_PATH=.*/APP_PATH=$APP_PATH/" "$here/deploy.env" && rm -f "$here/deploy.env.bak"
  fi
  echo "Generated APP_PATH (saved to deploy/deploy.env)."
fi
[[ "$APP_PATH" =~ ^[A-Za-z0-9_-]+$ ]] || { echo "APP_PATH must be URL-safe" >&2; exit 1; }

ssh -o StrictHostKeyChecking=accept-new "ubuntu@$DEPLOY_HOST" bash -s <<REMOTE
set -euo pipefail
cd /srv/calib
git fetch -q origin main
git reset -q --hard origin/main
umask 077
printf 'APP_HOST=%s\nAPP_PATH=%s\n' '$APP_HOST' '$APP_PATH' > deploy/.env
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build --remove-orphans
docker image prune -f >/dev/null
REMOTE

echo "Live at: https://$APP_HOST/$APP_PATH/"
