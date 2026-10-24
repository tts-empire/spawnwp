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
echo "==> Preparing a new immutable PHP image..."
python3 scripts/prepare-image.py --project . --version "$VER" --wordpress "$WP_VERSION" --refresh
echo "==> Done. Apply the prepared image explicitly to each site."
