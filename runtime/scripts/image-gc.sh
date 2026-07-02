#!/usr/bin/env bash
# Optional auto-delete of UNUSED wp-dev-php images (System info setting).
# SPAWNWP_IMAGE_AUTODELETE_DAYS=0 (the default) disables it entirely.
# An image is deleted only when BOTH hold:
#   - no site under /srv uses its PHP version (never touch referenced images);
#   - it is at least N days old.
# The next deploy on a deleted version simply rebuilds it (~5 min).
set -euo pipefail
source /etc/spawnwp/config.env

N="${SPAWNWP_IMAGE_AUTODELETE_DAYS:-0}"
if ! [[ "$N" =~ ^[0-9]+$ ]] || [ "$N" -eq 0 ]; then
  echo "image-gc: auto-delete disabled (SPAWNWP_IMAGE_AUTODELETE_DAYS=${N})"
  exit 0
fi

declare -A USED
for env_file in /srv/*/.env; do
  [ -f "$env_file" ] || continue
  ver=$(grep -E '^PHP_VERSION=' "$env_file" | head -1 | cut -d= -f2)
  [ -n "$ver" ] && USED["$ver"]=1
done

now=$(date +%s)
for tag in $(docker image ls wp-dev-php --format '{{.Tag}}'); do
  [[ "$tag" =~ ^[0-9]+\.[0-9]+$ ]] || continue
  if [ -n "${USED[$tag]:-}" ]; then
    echo "image-gc: keep wp-dev-php:${tag} (in use)"
    continue
  fi
  created=$(docker image inspect -f '{{.Created}}' "wp-dev-php:${tag}")
  age_days=$(( ( now - $(date -d "$created" +%s) ) / 86400 ))
  if [ "$age_days" -ge "$N" ]; then
    echo "image-gc: deleting wp-dev-php:${tag} (unused, ${age_days}d >= ${N}d)"
    docker rmi "wp-dev-php:${tag}" || echo "image-gc: could not delete wp-dev-php:${tag} (still referenced?)"
  else
    echo "image-gc: keep wp-dev-php:${tag} (unused but only ${age_days}d old)"
  fi
done
