#!/usr/bin/env bash
# Block until the WordPress CORE FILES exist in the php container.
#
# On a site's first start the image entrypoint extracts the whole WordPress
# install (thousands of files) into an empty volume. Nothing else waits for that:
# the php healthcheck is `php-fpm -t`, a config *syntax* test that passes
# immediately and says nothing about the volume. So bootstrap.sh could reach
# `wp core install` while the extraction was still running, and die with:
#
#   Error: This does not seem to be a WordPress installation.
#   The used path is: /var/www/html/
#
# failing the deploy and rolling the whole site back. Reported by @wpeasy
# (discussion #8); most likely right after an image rebuild, when the cold page
# cache makes the extraction slowest.
#
# The gate is wp-config.php + `wp core version`: the entrypoint writes wp-config.php
# only AFTER the core is in place, so seeing both means the extraction finished.
# Deliberately NOT `wp core is-installed` (what this script used to check, while
# no caller ever ran it): that additionally requires the database install which
# bootstrap.sh has not performed yet, so it can never be true here.
set -euo pipefail
cd "$(dirname "$0")/.."

TIMEOUT_SECONDS="${SPAWNWP_WP_WAIT_SECONDS:-300}"
deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))

while [ "$(date +%s)" -lt "$deadline" ]; do
  if docker compose exec -T -u www-data php test -s /var/www/html/wp-config.php 2>/dev/null \
     && docker compose exec -T -u www-data php wp core version >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done

echo "ERROR: the WordPress core files did not appear in /var/www/html within ${TIMEOUT_SECONDS}s." >&2
echo "       Check 'docker compose logs php': the container may have failed to extract WordPress." >&2
exit 1
