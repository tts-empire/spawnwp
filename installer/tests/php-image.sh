#!/usr/bin/env bash
# Assert a built SpawnWP php image carries the extensions sites depend on.
#
#   bash installer/tests/php-image.sh wp-dev-php:8.3 [8.3]
#
# Not part of CI: building the images takes minutes, and CI only compiles Python
# and builds the docs. Run it against every image before shipping an image change.
#
# History: 0.5.18 added ftp (+FTPS) after WordPress reported "ftp and ftps are not
# available", plus pdo_mysql — the image had only pdo_sqlite, so any plugin using
# PDO/MySQL failed while WordPress itself (mysqli) worked fine and hid the gap.
set -uo pipefail

IMAGE=${1:-}
PHP_VERSION=${2:-}

if [ -z "$IMAGE" ]; then
  echo "usage: $0 <image[:tag]> [php-version]" >&2
  exit 1
fi
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "ERROR: image '$IMAGE' not found. Build it first." >&2
  exit 1
}

# Derive the PHP version from the tag when not given (wp-dev-php:8.3 -> 8.3).
[ -n "$PHP_VERSION" ] || PHP_VERSION="${IMAGE##*:}"

REQUIRED_EXTS="ftp pdo_mysql soap sockets pcntl gmp calendar intl zip exif bcmath gd imagick redis mysqli"

failures=0
check() {
  if [ "$1" = "0" ]; then
    echo "PASS: $2"
  else
    echo "FAIL: $2"
    failures=$((failures + 1))
  fi
}

MODULES=$(docker run --rm --entrypoint php "$IMAGE" -m)
for ext in $REQUIRED_EXTS; do
  grep -qix "$ext" <<<"$MODULES"
  check $? "extension present: $ext"
done

FTP_RI=$(docker run --rm --entrypoint php "$IMAGE" --ri ftp 2>/dev/null)
grep -q 'FTP support => enabled' <<<"$FTP_RI"
check $? "FTP support enabled"

# FTPS works on every version we build (7.4/8.2/8.3/8.4/8.5) — the opt-in flag is just
# spelled differently before and after 8.4. See the Dockerfile comment.
grep -q 'FTPS support => enabled' <<<"$FTP_RI"
check $? "FTPS support enabled"

docker run --rm --entrypoint php "$IMAGE" -r 'exit(function_exists("ftp_connect") ? 0 : 1);'
check $? "ftp_connect() is callable"
docker run --rm --entrypoint php "$IMAGE" -r 'exit(function_exists("ftp_ssl_connect") ? 0 : 1);'
check $? "ftp_ssl_connect() is callable"

# Exercise the official entrypoint on an empty disposable docroot. Checksums alone
# cannot detect a missing Docker config template after replacing a pinned core.
docker run --rm --entrypoint bash \
  -e WORDPRESS_DB_HOST=spawnwp-test-db:3306 \
  -e WORDPRESS_DB_NAME=spawnwp_test \
  -e WORDPRESS_DB_USER=spawnwp_test \
  -e WORDPRESS_DB_PASSWORD=disposable-test-password \
  -e WORDPRESS_CONFIG_EXTRA="define('SPAWNWP_IMAGE_BOOT_TEST', true);" \
  "$IMAGE" -ec '
    docker-entrypoint.sh php-fpm -t
    test -s wp-config.php
    test "$(wp config get DB_HOST --allow-root)" = "spawnwp-test-db:3306"
    grep -q WORDPRESS_CONFIG_EXTRA wp-config.php
  '
check $? "first boot creates Docker-aware wp-config.php and validates PHP-FPM"

echo
if [ "$failures" -ne 0 ]; then
  echo "$failures check(s) failed for $IMAGE" >&2
  exit 1
fi
echo "All php-image checks passed for $IMAGE (PHP $PHP_VERSION)."
