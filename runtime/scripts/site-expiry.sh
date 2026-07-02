#!/usr/bin/env bash
# Destroy expired temporary sites (SPAWNWP_EXPIRES in a site's .env, written at
# creation when a lifetime is chosen, or later via the cockpit). Runs hourly via
# spawnwp-site-expiry.timer. Destruction is complete and final — containers,
# volumes, directory and nginx block — exactly like a manual Destroy; temporary
# sites keep no backups by design. The primary stack is never touched.
set -euo pipefail

PRIMARY="/srv/wp-dev"
now=$(date +%s)

for env_file in /srv/*/.env; do
  [ -f "$env_file" ] || continue
  proj_dir=$(dirname "$env_file")
  [ "$proj_dir" = "$PRIMARY" ] && continue
  [ -f "$proj_dir/compose.yaml" ] || continue
  expires=$(grep -E '^SPAWNWP_EXPIRES=' "$env_file" | head -1 | cut -d= -f2)
  [[ "$expires" =~ ^[0-9]+$ ]] || continue
  if [ "$now" -ge "$expires" ]; then
    name=$(basename "$proj_dir")
    echo "site-expiry: '${name}' expired on $(date -d "@${expires}" '+%Y-%m-%d %H:%M'), destroying..."
    (cd "$proj_dir" && docker compose down --remove-orphans) || true
    bash "${PRIMARY}/scripts/destroy-project.sh" "$name" --yes \
      || echo "site-expiry: failed to destroy '${name}' (will retry next hour)" >&2
  fi
done
echo "site-expiry: check complete."
