#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source /etc/spawnwp/config.env
export DOCKER_CONFIG=${DOCKER_CONFIG:-/var/lib/spawnwp/docker}
python3 -c 'from service_images import prepare_adminer; import json; print(json.dumps(prepare_adminer()))'
echo "==> Prepared and verified Adminer. Apply it explicitly to each site."
