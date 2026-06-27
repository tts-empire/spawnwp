#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[ "${1:-}" = "--force" ] || { echo "Run with --force to confirm." >&2; exit 1; }
bash scripts/snapshot.sh "pre-reset-$(date +%Y%m%d-%H%M%S)" || true
docker compose down -v
echo "==> Reset complete. Run make up && make bootstrap."
