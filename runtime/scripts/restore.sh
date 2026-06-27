#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .env
NAME="${1:-}"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "ERROR: invalid snapshot name" >&2; exit 1; }
DB_FILE="backups/db/$NAME.sql.gz"
[ -f "$DB_FILE" ] || { echo "ERROR: snapshot not found" >&2; exit 1; }
gunzip -c "$DB_FILE" | docker compose exec -T db mariadb -u"$DB_USER" -p"$DB_PASS" "$DB_NAME"
if [ -f "backups/files/$NAME.tar.gz" ]; then
  tar xzf "backups/files/$NAME.tar.gz" -C projects/primary/wp-content
  chown -R 33:33 projects/primary/wp-content/uploads 2>/dev/null || true
fi
docker compose exec -T -u www-data php wp cache flush 2>/dev/null || true
echo "==> Restore complete: $NAME"
