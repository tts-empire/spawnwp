#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env
export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}

echo "==> Pulling the latest Adminer image..."
docker compose pull adminer
VERSION=$(docker run --rm --network none --read-only --memory 128m \
  --cpus 0.5 --pids-limit 16 --user 65534:65534 --cap-drop ALL \
  --security-opt no-new-privileges --entrypoint php adminer:5 -r 'echo PHP_VERSION;')
python3 php_security.py --check-version "$VERSION"
echo "==> Adminer image is ready: PHP ${VERSION}. Existing Adminer containers are unchanged until an administrator applies the update."
