#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .env
NAME="${1:-$(date +%Y%m%d-%H%M%S)}"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: invalid snapshot name" >&2; exit 1; }
mkdir -p backups/db backups/files
docker compose exec -T db mariadb-dump -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" | gzip > "backups/db/$NAME.sql.gz"
if [ "${INCLUDE_FILES:-0}" = "1" ]; then
  tar czf "backups/files/$NAME.tar.gz" -C projects/primary/wp-content uploads/ 2>/dev/null || true
fi
find backups/db -maxdepth 1 -name '*.sql.gz' -printf '%T@ %p\n' | sort -rn | tail -n +11 | cut -d' ' -f2- | xargs -r rm --
echo "==> Snapshot complete: $NAME"
