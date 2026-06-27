#!/usr/bin/env bash
set -euo pipefail
for _ in $(seq 1 60); do
  if docker compose exec -T -u www-data php wp core is-installed 2>/dev/null; then exit 0; fi
  sleep 3
done
echo "ERROR: WordPress not ready after 180 seconds" >&2
exit 1
