#!/usr/bin/env bash
# Rebuild a wp-dev-php image with the latest WordPress base, on demand (System
# info tab). Replaces the old automatic 7-day refresh: freshness is now the
# admin's explicit choice. Existing containers keep the old image until their
# next recreate; new deploys pick the refreshed one immediately.
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env
source "$(pwd)/scripts/lib-image.sh"

export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}

VER="${1:-}"
if [[ ! "$VER" =~ ^[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <php-version>   (e.g. $0 8.3)" >&2
  exit 1
fi

# WP build args: same values new-project.sh uses (blueprints share them), read
# from the primary .env with the compose defaults as fallback, so the context
# hash stamped here matches the one the create gate computes.
#
# `|| true` is load-bearing. When the primary project has no .env (it is often a
# scaffold used only to build images) grep exits 2, not 1 — under `pipefail` the
# pipeline inherits that, and under `set -e` the assignment aborts the script
# before the ${WP_VERSION:-latest} fallback below can run. 2>/dev/null hides
# grep's message, not its exit status. That is what made System → Refresh fail
# with a bare "Exited with code 2" (reported by @wpeasy, discussion #8).
WP_VERSION=$(grep -E '^WP_VERSION=' .env 2>/dev/null | cut -d= -f2 || true)
export WP_VERSION="${WP_VERSION:-latest}"
export PHP_VERSION="$VER"
export WP_IMAGE_SUFFIX=$(spawnwp_wp_suffix "$WP_VERSION")

IMAGE=$(spawnwp_image_tag "$VER" "$WP_VERSION")
CONTEXT_HASH=$(spawnwp_context_hash "$WP_VERSION")
export SPAWNWP_CONTEXT_HASH="$CONTEXT_HASH"

echo "==> Refreshing ${IMAGE}: pulling the latest base and rebuilding..."
docker compose build --pull php
docker builder prune -f --filter until=24h >/dev/null 2>&1 || true
{ source "$(pwd)/scripts/lib-metrics.sh" 2>/dev/null && metric_incr image_refreshes; } || true
echo "==> Done: ${IMAGE} refreshed with the latest WordPress."
