#!/usr/bin/env bash
# Rebuild a selected PHP/WordPress image with the latest base, on demand. This
# prepares an image only; applying it to an existing site is a separate action.
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env
source "$(pwd)/scripts/lib-image.sh"

export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}

VER="${1:-}"
WP_VERSION="${2:-latest}"
if [[ ! "$VER" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <php-version>   (e.g. $0 8.3)" >&2
  exit 1
fi

# Preserve the selected site's pinned WordPress core when refreshing its image.
if [[ ! "$WP_VERSION" =~ ^([0-9]+(\.[0-9]+){0,2}|latest)$ ]]; then
  echo "Invalid WordPress version" >&2
  exit 1
fi
export PHP_VERSION="$VER"
export WP_VERSION
export WP_IMAGE_SUFFIX=$(spawnwp_wp_suffix "$WP_VERSION")

IMAGE=$(spawnwp_image_tag "$VER" "$WP_VERSION")
CONTEXT_HASH=$(spawnwp_context_hash "$WP_VERSION")
export SPAWNWP_CONTEXT_HASH="$CONTEXT_HASH"

echo "==> Refreshing ${IMAGE}: pulling the latest base and rebuilding..."
docker compose build --pull php
docker builder prune -f --filter until=24h >/dev/null 2>&1 || true
{ source "$(pwd)/scripts/lib-metrics.sh" 2>/dev/null && metric_incr image_refreshes; } || true
echo "==> Done: ${IMAGE} refreshed. Running sites keep their current image until an administrator applies the update."
