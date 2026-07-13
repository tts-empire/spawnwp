#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .env
WP=(docker compose exec -T -u www-data php wp)

echo "==> Waiting for MariaDB..."
until docker compose exec -T db mariadb -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "SELECT 1" &>/dev/null; do sleep 2; done
# ...and for WordPress itself. Waiting only for the database used to let this
# script race the php entrypoint's first-run extraction of the core files and
# fail with "This does not seem to be a WordPress installation" (discussion #8).
echo "==> Waiting for the WordPress core files..."
bash scripts/wait-for-wordpress.sh
docker compose exec -T -u www-data php chmod -R a+rX /var/www/html 2>/dev/null || true
if "${WP[@]}" core is-installed 2>/dev/null; then
  echo "WordPress already installed - skipping bootstrap."
  exit 0
fi
echo "==> Installing WordPress..."
"${WP[@]}" core install --url="$WP_HOME" --title="SpawnWP Development" \
  --admin_user="$WP_ADMIN_USER" --admin_password="$WP_ADMIN_PASS" \
  --admin_email="$WP_ADMIN_EMAIL" --skip-email
"${WP[@]}" rewrite structure "/%postname%/" --hard
"${WP[@]}" option update blogdescription ""
"${WP[@]}" option update default_pingback_flag 0
"${WP[@]}" option update default_ping_status closed
"${WP[@]}" option update default_comment_status closed
"${WP[@]}" config set SMTP_HOST mailpit
"${WP[@]}" config set SMTP_PORT 1025 --raw
"${WP[@]}" post delete 1 --force 2>/dev/null || true
"${WP[@]}" comment delete 1 --force 2>/dev/null || true
echo "==> Bootstrap complete: $WP_HOME/wp-admin/ ($WP_ADMIN_USER)"
