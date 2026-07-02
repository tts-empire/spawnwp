#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env

export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}
install -d -m 0700 "$DOCKER_CONFIG"

NAME="${1:-}"
BLUEPRINT="${2:-development}"
PHP_OVERRIDE="${3:-}"
if [ -z "$NAME" ]; then
  echo "Usage: $0 <project-name> [blueprint] [php-version]" >&2
  echo "  Example: $0 demo-site development 8.3" >&2
  exit 1
fi
if [[ ! "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]]; then
  echo "ERROR: invalid project name; use lowercase letters, digits and hyphens only." >&2
  exit 1
fi

PROJ_DIR="/srv/${NAME}"
NGINX_CONF="/etc/nginx/sites-available/spawnwp"
if [ ! -e /etc/nginx/sites-enabled/spawnwp ] && [ -e /etc/nginx/sites-enabled/default ]; then
  NGINX_CONF=$(readlink -f /etc/nginx/sites-enabled/default)
fi

RESOLVE_ARGS=(resolve "$BLUEPRINT" --output /tmp/spawnwp-blueprint-$$.json --shell)
if [ -n "$PHP_OVERRIDE" ]; then
  RESOLVE_ARGS+=(--php "$PHP_OVERRIDE")
fi
eval "$(python3 scripts/blueprint.py "${RESOLVE_ARGS[@]}")"
RESOLVED_BLUEPRINT="/tmp/spawnwp-blueprint-$$.json"

exec 9>/run/lock/spawnwp-new-project.lock
if ! flock -n 9; then
  echo "ERROR: another site creation is already in progress." >&2
  rm -f "$RESOLVED_BLUEPRINT"
  exit 1
fi

NGINX_BACKUP=$(mktemp)
cp "$NGINX_CONF" "$NGINX_BACKUP"
PROJECT_CREATED=0
FINISHED=0
cleanup() {
  rc=$?
  if [ "$FINISHED" = "0" ]; then
    echo "!! Creation failed; rolling back partial resources..." >&2
    if [ "$PROJECT_CREATED" = "1" ] && [ -d "$PROJ_DIR" ]; then
      (cd "$PROJ_DIR" && docker compose down -v --remove-orphans) >/dev/null 2>&1 || true
      rm -rf "$PROJ_DIR"
    fi
    cp "$NGINX_BACKUP" "$NGINX_CONF"
    nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
  fi
  rm -f "$NGINX_BACKUP" "$RESOLVED_BLUEPRINT"
  exit "$rc"
}
trap cleanup EXIT INT TERM

# Find next available web port starting from 8081
PORT=8081
while ss -tlnp | grep -q ":${PORT}\b"; do
  PORT=$((PORT + 1))
done

# Find next available mailpit port starting from 8026 (8025 = primary)
MAILPIT_PORT=8026
while ss -tlnp | grep -q ":${MAILPIT_PORT}\b"; do
  MAILPIT_PORT=$((MAILPIT_PORT + 1))
done

# Find next available adminer port starting from 9002 (9001 = primary)
ADMINER_PORT=9002
while ss -tlnp | grep -q ":${ADMINER_PORT}\b"; do
  ADMINER_PORT=$((ADMINER_PORT + 1))
done

if [ -d "$PROJ_DIR" ]; then
  echo "ERROR: $PROJ_DIR already exists." >&2
  exit 1
fi

echo "==> Creating project '$NAME' with blueprint '${BLUEPRINT_ID}' ${BLUEPRINT_VERSION} on PHP ${PHP_VERSION}"
echo "    URL: https://${DOMAIN}/${NAME}/ | port ${PORT}"

# Generate secrets
DB_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
DB_ROOT_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
WP_ADMIN_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)
REDIS_PASS=$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)

# Admin user randomized from the site name: no predictable 'admin'.
# E.g. site 'bellu' -> 'bellu-a3f9c1'. Recoverable from the cockpit (reads .env).
WP_ADMIN_USER="${NAME}-$(openssl rand -hex 3)"

# Copy stack structure
mkdir -p "${PROJ_DIR}"
PROJECT_CREATED=1
cp compose.yaml "${PROJ_DIR}/"
cp -r docker "${PROJ_DIR}/"
# Pre-0.3.13 stacks mounted a docker/mariadb/custom.cnf that never existed, so
# Docker auto-created an empty DIRECTORY with that name; don't propagate it.
rm -rf "${PROJ_DIR}/docker/mariadb/custom.cnf"
cp Makefile "${PROJ_DIR}/"
cp -r scripts "${PROJ_DIR}/"
mkdir -p "${PROJ_DIR}/projects/primary/wp-content/plugins"
mkdir -p "${PROJ_DIR}/backups/db" "${PROJ_DIR}/backups/files"
mkdir -p "${PROJ_DIR}/.spawnwp"
install -m 0644 "$RESOLVED_BLUEPRINT" "${PROJ_DIR}/.spawnwp/blueprint.json"

# Write .env
cat > "${PROJ_DIR}/.env" <<EOF
COMPOSE_PROJECT_NAME=${NAME}
PHP_VERSION=${PHP_VERSION}
WP_VERSION=${WP_VERSION}
WORDPRESS_SERIES=${WORDPRESS_SERIES}
WP_DEBUG=${WP_DEBUG_VALUE}
SPAWNWP_BLUEPRINT=${BLUEPRINT_ID}
SPAWNWP_BLUEPRINT_VERSION=${BLUEPRINT_VERSION}
DB_NAME=wordpress
DB_USER=wpuser
DB_PASS=${DB_PASS}
DB_ROOT_PASS=${DB_ROOT_PASS}
DB_TABLE_PREFIX=wp_
WP_ADMIN_USER=${WP_ADMIN_USER}
WP_ADMIN_EMAIL=${EMAIL}
WP_ADMIN_PASS=${WP_ADMIN_PASS}
WP_HOME=https://${DOMAIN}/${NAME}
WP_SITEURL=https://${DOMAIN}/${NAME}
WEB_PORT=${PORT}
MAILPIT_PORT=${MAILPIT_PORT}
MAILPIT_WEBROOT=/${NAME}-mail
ADMINER_PORT=${ADMINER_PORT}
REDIS_PASSWORD=${REDIS_PASS}
EOF

cp env.example "${PROJ_DIR}/.env.example"
cp gitignore.template "${PROJ_DIR}/.gitignore"

# Add Nginx location blocks using Python (handles multiline safely).
# Two server blocks now: the WordPress site goes on the devel vhost; Adminer +
# Mailpit go on the cockpit subdomain vhost (session-protected and same-origin as
# the dashboard so the Adminer auto-login stays same-origin).
python3 - <<PYEOF
conf_path = "${NGINX_CONF}"
with open(conf_path) as f:
    conf = f.read()

# WordPress site → devel vhost (before @wp_down)
wp_block = """
    # >>> SPAWNWP SITE ${NAME}
    # The deploy plugin authenticates requests with signed keys, timestamps and nonces.
    location /${NAME}/wp-json/spawnwp-deploy/v1/ {
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_set_header X-Forwarded-Prefix /${NAME};
        proxy_pass http://127.0.0.1:${PORT}/wp-json/spawnwp-deploy/v1/;
        proxy_intercept_errors on;
        error_page 502 503 504 =502 @wp_down;
    }

    # ── ${NAME} (port ${PORT}) ──────────────────────────────────────────────
    location /${NAME}/ {
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_set_header X-Forwarded-Prefix /${NAME};
        proxy_pass http://127.0.0.1:${PORT}/;
        # On the /wp-admin directory redirect, the backend (internal nginx) emits
        # an absolute http Location WITHOUT the prefix (e.g. http://host/wp-admin/):
        # we rewrite it, re-adding /${NAME} and forcing https. WordPress' own
        # redirects are already https+prefix, so they are not touched.
        proxy_redirect http://${DOMAIN}/ https://${DOMAIN}/${NAME}/;
        proxy_intercept_errors on;
        error_page 502 503 504 =502 @wp_down;
    }
    # <<< SPAWNWP SITE ${NAME}

"""
conf = conf.replace("    location @wp_down {", wp_block + "    location @wp_down {", 1)

# Adminer + Mailpit → cockpit subdomain vhost (before the __COCKPIT_PER_SITE__ marker)
admin_block = """    # >>> SPAWNWP ADMIN ${NAME}
    # ── ${NAME} admin ──
    location /${NAME}-db/ {
        auth_request /_spawnwp_auth;
        error_page 401 = @spawnwp_login;
        proxy_pass http://127.0.0.1:${ADMINER_PORT}/;
        add_header Cache-Control "no-store" always;
    }
    location /${NAME}-mail/ {
        auth_request /_spawnwp_auth;
        error_page 401 = @spawnwp_login;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:${MAILPIT_PORT};
        add_header Cache-Control "no-store" always;
    }
    # <<< SPAWNWP ADMIN ${NAME}
"""
conf = conf.replace("    # __COCKPIT_PER_SITE__", admin_block + "    # __COCKPIT_PER_SITE__", 1)

with open(conf_path, "w") as f:
    f.write(conf)
PYEOF

echo "  -> Nginx updated. Testing config..."
nginx -t
systemctl reload nginx
echo "  -> Nginx reloaded."

echo ""
echo "==> Project '$NAME' created at: ${PROJ_DIR}"
echo "    URL:  https://${DOMAIN}/${NAME}/"
echo "    Port: ${PORT}"
echo ""
echo "    Next steps:"
echo "      cd ${PROJ_DIR}"
echo "      make up"
echo "      make bootstrap"
echo ""
echo "==> Starting stack (db + php first, then the rest)..."
cd "${PROJ_DIR}"

# Build the php image only when needed. The image label records a hash of the
# docker/php build context plus the WP build args; when it matches and the image
# is recent we reuse it instead of paying a full 'build --pull' per create (that
# used to cost minutes and grow the BuildKit cache without bound). Freshness is
# preserved by an age-based refresh with --pull (default 7 days, tunable via
# SPAWNWP_IMAGE_MAX_AGE_DAYS) — same guarantee that once avoided a stale WP 6.9.4,
# paid at most once a week instead of on every site.
# Escape hatch: SPAWNWP_REBUILD=1 forces a fresh build.
IMAGE="wp-dev-php:${PHP_VERSION}"
CONTEXT_HASH=$( { cd docker/php && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum; \
                  echo "series=${WORDPRESS_SERIES} wp=${WP_VERSION}"; } | sha256sum | cut -c1-12 )
export SPAWNWP_CONTEXT_HASH="$CONTEXT_HASH"
MAX_AGE_DAYS="${SPAWNWP_IMAGE_MAX_AGE_DAYS:-7}"
NEED_BUILD=0
BUILD_ARGS=()
if [ "${SPAWNWP_REBUILD:-0}" = "1" ]; then
  echo "==> SPAWNWP_REBUILD=1: forcing a fresh php image build..."
  NEED_BUILD=1; BUILD_ARGS=(--pull)
elif ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "==> php image ${IMAGE} not present: building (first use of PHP ${PHP_VERSION})..."
  NEED_BUILD=1; BUILD_ARGS=(--pull)
else
  IMAGE_HASH=$(docker image inspect -f '{{index .Config.Labels "org.spawnwp.context-hash"}}' "$IMAGE" 2>/dev/null || true)
  CREATED=$(docker image inspect -f '{{.Created}}' "$IMAGE")
  AGE_DAYS=$(( ( $(date +%s) - $(date -d "$CREATED" +%s) ) / 86400 ))
  if [ "$IMAGE_HASH" != "$CONTEXT_HASH" ]; then
    echo "==> php build context changed: rebuilding ${IMAGE}..."
    NEED_BUILD=1
  elif [ "$AGE_DAYS" -ge "$MAX_AGE_DAYS" ]; then
    echo "==> php image is ${AGE_DAYS} days old: refreshing base (latest WordPress)..."
    NEED_BUILD=1; BUILD_ARGS=(--pull)
  else
    echo "==> Reusing php image ${IMAGE} (context unchanged, ${AGE_DAYS}d old)."
  fi
fi
if [ "$NEED_BUILD" = "1" ]; then
  docker compose build ${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"} php
  # Trim the BuildKit cache now that the image is tagged. 'until' counts from last
  # use, so the layers this build just touched survive and future rebuilds still
  # hit the cache; only stale orphaned layers are dropped.
  docker builder prune -f --filter until=24h >/dev/null 2>&1 || true
fi

# Deterministic two-phase startup. On the FIRST run the WordPress entrypoint
# extracts the whole install into the empty volume (thousands of files): under
# that I/O load php's healthcheck can time out. If we started everything at once,
# nginx (depends_on php: service_healthy) would give up and 'make up' would abort
# leaving the site half-done. So: php first (brings up db too), wait until it is
# healthy, then start nginx/mailpit/adminer.
docker compose up -d php

echo "==> Waiting for php to be healthy (WordPress extraction on first run)..."
PHP_HEALTHY=false
for _ in $(seq 1 60); do
  HEALTH=$(docker inspect -f '{{.State.Health.Status}}' "${NAME}-php-1" 2>/dev/null || echo "starting")
  if [ "$HEALTH" = "healthy" ]; then
    PHP_HEALTHY=true
    echo "  -> php healthy."
    break
  fi
  sleep 3
done
if [ "$PHP_HEALTHY" != "true" ]; then
  echo "  !! php did not become healthy in time; continuing anyway (check 'docker logs ${NAME}-php-1')." >&2
fi

echo "==> Starting the remaining services (nginx, mailpit, adminer)..."
docker compose up -d

echo "==> Bootstrap WordPress..."
make bootstrap

echo "==> Applying resolved blueprint..."
bash scripts/apply-blueprint.sh .spawnwp/blueprint.json

# The WordPress image populates the volume with 0600 files: nginx (a different
# uid) cannot read them. Make them readable (run as www-data, the owner).
echo "==> Fixing core file permissions for nginx..."
docker compose exec -T -u www-data php chmod -R a+rX /var/www/html 2>/dev/null || true

# The wp-content bind mount must belong to uid 33 (www-data in the container)
# so PHP can write uploads, cache, plugin temp, etc.
echo "==> Fixing wp-content bind mount ownership (uid 33)..."
chown -R 33:33 "${PROJ_DIR}/projects/primary/wp-content"

echo ""
echo "==> Done! Site available at: https://${DOMAIN}/${NAME}/"
echo ""
echo "    Admin credentials:"
echo "      URL:  https://${DOMAIN}/${NAME}/wp-admin/"
echo "      User: ${WP_ADMIN_USER}"
echo "      Pass: ${WP_ADMIN_PASS}"
echo "    Blueprint: ${BLUEPRINT_NAME} ${BLUEPRINT_VERSION}"

FINISHED=1
