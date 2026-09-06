#!/usr/bin/env bash
# Apply per-site PHP settings to an EXISTING site (cockpit Manage → PHP settings).
# Rewrites docker/php/zz-site.ini from the SPAWNWP_PHP_* env vars, aligns the
# nginx body-size limits (site container + host proxy), records the derived PHP
# cgroup cap and recreates php only when that service is already running. Sites
# that are Down stay Down. Sites created before 0.3.14 lack the zz-site.ini mount
# in their frozen compose.yaml, so we refuse with a clear message.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib-php-ini.sh"

NAME="${1:-}"
PROJ_DIR="/srv/${NAME}"
if [ -z "$NAME" ] || [ ! -f "${PROJ_DIR}/compose.yaml" ]; then
  echo "Usage: $0 <site-name>" >&2
  exit 1
fi
if ! grep -q "zz-site.ini" "${PROJ_DIR}/compose.yaml"; then
  echo "ERROR: this site was created before SpawnWP 0.3.14 and has no per-site PHP overrides mount. Recreate it to use PHP settings." >&2
  exit 2
fi

php_ini_defaults
write_php_ini "${PROJ_DIR}"
CONTAINER_MEMORY="${SPAWNWP_PHP_CONTAINER_MEMORY:-512M}"
[[ "$CONTAINER_MEMORY" =~ ^[0-9]{3,4}M$ ]] || { echo "ERROR: invalid derived PHP container memory" >&2; exit 2; }
if grep -q '^SPAWNWP_PHP_CONTAINER_MEMORY=' "${PROJ_DIR}/.env"; then
  sed -i "s/^SPAWNWP_PHP_CONTAINER_MEMORY=.*/SPAWNWP_PHP_CONTAINER_MEMORY=${CONTAINER_MEMORY}/" "${PROJ_DIR}/.env"
else
  echo "SPAWNWP_PHP_CONTAINER_MEMORY=${CONTAINER_MEMORY}" >> "${PROJ_DIR}/.env"
fi
sync_nginx_body_size "${PROJ_DIR}" "${NAME}"
nginx -t
systemctl reload nginx

cd "${PROJ_DIR}"
if docker compose ps --services --filter status=running | grep -qx php; then
  docker compose up -d php
  echo "==> PHP settings applied to '${NAME}' (php recreated)."
else
  echo "==> PHP settings saved for '${NAME}'. The site is Down and was left Down."
fi
