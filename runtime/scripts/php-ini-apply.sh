#!/usr/bin/env bash
# Apply per-site PHP settings to an EXISTING site (cockpit Manage → PHP settings).
# Rewrites docker/php/zz-site.ini from the SPAWNWP_PHP_* env vars, aligns the
# nginx body-size limits (site container + host proxy) and restarts only the
# php container (~2s). Sites created before 0.3.14 lack the zz-site.ini mount
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
sync_nginx_body_size "${PROJ_DIR}" "${NAME}"
nginx -t
systemctl reload nginx

cd "${PROJ_DIR}"
docker compose restart php
echo "==> PHP settings applied to '${NAME}' (php restarted)."
