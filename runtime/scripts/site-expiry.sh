#!/usr/bin/env bash
# Destroy expired temporary sites (SPAWNWP_EXPIRES in a site's .env, written at
# creation when a lifetime is chosen, or later via the cockpit). Runs every five minutes via
# spawnwp-site-expiry.timer. Destruction is complete and final — containers,
# volumes, directory and nginx block — exactly like a manual Destroy; temporary
# sites keep no backups by design. The primary stack is never touched.
set -euo pipefail

PROJECTS_ROOT="${SPAWNWP_PROJECTS_ROOT:-/srv}"
PRIMARY="${SPAWNWP_PRIMARY_PROJECT:-${PROJECTS_ROOT}/wp-dev}"
PROJECT_LOCK="${SPAWNWP_PROJECT_LOCK:-/run/lock/spawnwp-projects.lock}"
now="${SPAWNWP_NOW:-$(date +%s)}"

exec 9>"$PROJECT_LOCK"
if ! flock -n 9; then
  echo "site-expiry: another site operation is in progress; will retry next run."
  exit 0
fi

for env_file in "$PROJECTS_ROOT"/*/.env; do
  [ -f "$env_file" ] || continue
  proj_dir=$(dirname "$env_file")
  [ "$proj_dir" = "$PRIMARY" ] && continue
  [ -f "$proj_dir/compose.yaml" ] || continue
  expires=""
  while IFS='=' read -r key value || [ -n "$key" ]; do
    if [ "$key" = "SPAWNWP_EXPIRES" ]; then
      expires="$value"
      break
    fi
  done < "$env_file"
  [[ "$expires" =~ ^[0-9]+$ ]] || continue
  if [ "$now" -ge "$expires" ]; then
    name=$(basename "$proj_dir")
    echo "site-expiry: '${name}' expired on $(date -d "@${expires}" '+%Y-%m-%d %H:%M'), destroying..."
    (cd "$proj_dir" && docker compose down --remove-orphans) || true
    if SPAWNWP_PROJECT_LOCK_HELD=1 bash "${PRIMARY}/scripts/destroy-project.sh" "$name" --yes; then
      source "${PRIMARY}/scripts/lib-metrics.sh" 2>/dev/null && metric_incr sites_expired_auto
    else
      echo "site-expiry: failed to destroy '${name}' (will retry next run)" >&2
    fi
  fi
done
echo "site-expiry: check complete."
