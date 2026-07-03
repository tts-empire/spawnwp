#!/usr/bin/env bash
# Rebuild a wp-dev-php image with the latest WordPress base, on demand (System
# info tab). Replaces the old automatic 7-day refresh: freshness is now the
# admin's explicit choice. Existing containers keep the old image until their
# next recreate; new deploys pick the refreshed one immediately.
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env

export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}

VER="${1:-}"
if [[ ! "$VER" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <php-version>   (e.g. $0 8.3)" >&2
  exit 1
fi

# WP build args: same values new-project.sh uses (blueprints share them), read
# from the primary .env with the compose defaults as fallback, so the context
# hash stamped here matches the one the create gate computes.
WORDPRESS_SERIES=$(grep -E '^WORDPRESS_SERIES=' .env 2>/dev/null | cut -d= -f2)
WP_VERSION=$(grep -E '^WP_VERSION=' .env 2>/dev/null | cut -d= -f2)
export WORDPRESS_SERIES="${WORDPRESS_SERIES:-7}"
export WP_VERSION="${WP_VERSION:-latest}"
export PHP_VERSION="$VER"

IMAGE="wp-dev-php:${VER}"
# zz-site.ini is a runtime mount, excluded from the hash exactly as in new-project.sh.
CONTEXT_HASH=$( { cd docker/php && find . -type f ! -name 'zz-site.ini' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum; \
                  echo "series=${WORDPRESS_SERIES} wp=${WP_VERSION}"; } | sha256sum | cut -c1-12 )
export SPAWNWP_CONTEXT_HASH="$CONTEXT_HASH"

echo "==> Refreshing ${IMAGE}: pulling the latest base and rebuilding..."
docker compose build --pull php
docker builder prune -f --filter until=24h >/dev/null 2>&1 || true
{ source "$(pwd)/scripts/lib-metrics.sh" 2>/dev/null && metric_incr image_refreshes; } || true
echo "==> Done: ${IMAGE} refreshed with the latest WordPress."
