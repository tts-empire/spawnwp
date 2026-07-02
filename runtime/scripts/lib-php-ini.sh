# Shared generator for the per-site PHP overrides (zz-site.ini).
# Sourced by new-project.sh (at create time) and php-ini-apply.sh (post-create
# edits). Values arrive as SPAWNWP_PHP_* env vars, already validated by the
# cockpit; the light checks here only protect direct CLI use. The file is
# MOUNTED into the php container (conf.d loads alphabetically, so zz- wins
# over the baked custom.ini) — changing it never rebuilds the image.

php_ini_defaults() {
  PHP_MEMORY_LIMIT="${SPAWNWP_PHP_MEMORY_LIMIT:-256M}"
  PHP_UPLOAD_MAX_FILESIZE="${SPAWNWP_PHP_UPLOAD_MAX_FILESIZE:-64M}"
  PHP_POST_MAX_SIZE="${SPAWNWP_PHP_POST_MAX_SIZE:-64M}"
  PHP_MAX_EXECUTION_TIME="${SPAWNWP_PHP_MAX_EXECUTION_TIME:-120}"
  PHP_MAX_INPUT_VARS="${SPAWNWP_PHP_MAX_INPUT_VARS:-3000}"
  PHP_MAX_INPUT_TIME="${SPAWNWP_PHP_MAX_INPUT_TIME:--1}"
  PHP_DISPLAY_ERRORS="${SPAWNWP_PHP_DISPLAY_ERRORS:-Off}"

  local size='^[0-9]{1,4}[KMG]$'
  [[ "$PHP_MEMORY_LIMIT" =~ $size ]] || { echo "ERROR: invalid memory_limit '${PHP_MEMORY_LIMIT}'" >&2; return 1; }
  [[ "$PHP_UPLOAD_MAX_FILESIZE" =~ $size ]] || { echo "ERROR: invalid upload_max_filesize '${PHP_UPLOAD_MAX_FILESIZE}'" >&2; return 1; }
  [[ "$PHP_POST_MAX_SIZE" =~ $size ]] || { echo "ERROR: invalid post_max_size '${PHP_POST_MAX_SIZE}'" >&2; return 1; }
  [[ "$PHP_MAX_EXECUTION_TIME" =~ ^[0-9]{1,4}$ ]] || { echo "ERROR: invalid max_execution_time" >&2; return 1; }
  [[ "$PHP_MAX_INPUT_VARS" =~ ^[0-9]{3,6}$ ]] || { echo "ERROR: invalid max_input_vars" >&2; return 1; }
  [[ "$PHP_MAX_INPUT_TIME" =~ ^-?[0-9]{1,4}$ ]] || { echo "ERROR: invalid max_input_time" >&2; return 1; }
  [[ "$PHP_DISPLAY_ERRORS" =~ ^(On|Off)$ ]] || { echo "ERROR: invalid display_errors (On/Off)" >&2; return 1; }
}

# write_php_ini <project-dir>: writes docker/php/zz-site.ini from PHP_* vars.
write_php_ini() {
  local proj_dir="$1"
  cat > "${proj_dir}/docker/php/zz-site.ini" <<INI
; Per-site PHP overrides managed by SpawnWP (cockpit: Deploy / PHP settings).
; Loaded after custom.ini; edit from the cockpit, not by hand.
memory_limit = ${PHP_MEMORY_LIMIT}
upload_max_filesize = ${PHP_UPLOAD_MAX_FILESIZE}
post_max_size = ${PHP_POST_MAX_SIZE}
max_execution_time = ${PHP_MAX_EXECUTION_TIME}
max_input_vars = ${PHP_MAX_INPUT_VARS}
max_input_time = ${PHP_MAX_INPUT_TIME}
display_errors = ${PHP_DISPLAY_ERRORS}
INI
  chmod 0644 "${proj_dir}/docker/php/zz-site.ini"
}

# php_ini_body_size: echoes the nginx client_max_body_size matching the PHP
# limits (the larger of upload/post, lowercase unit).
php_ini_body_size() {
  local upload="${PHP_UPLOAD_MAX_FILESIZE}" post="${PHP_POST_MAX_SIZE}"
  local up_mb post_mb
  up_mb=$(php_ini_to_mb "$upload"); post_mb=$(php_ini_to_mb "$post")
  if [ "$up_mb" -ge "$post_mb" ]; then echo "${upload,,}"; else echo "${post,,}"; fi
}

php_ini_to_mb() {
  local v="$1" n="${1%[KMG]}"
  case "$v" in
    *K) echo $(( n / 1024 )) ;;
    *G) echo $(( n * 1024 )) ;;
    *)  echo "${n}" ;;
  esac
}

# sync_nginx_body_size <project-dir> <site-name>: aligns the site container's
# nginx and the host proxy location with the PHP limits, so raising the upload
# size actually works end to end.
sync_nginx_body_size() {
  local proj_dir="$1" name="$2" body
  body=$(php_ini_body_size)
  sed -i "s/^\(\s*client_max_body_size\s\+\).*/\1${body};/" "${proj_dir}/docker/nginx/default.conf"
  # Same vhost resolution as new-project.sh: what matters is which conf is ENABLED.
  local host_conf="/etc/nginx/sites-available/spawnwp"
  if [ ! -e /etc/nginx/sites-enabled/spawnwp ] && [ -e /etc/nginx/sites-enabled/default ]; then
    host_conf=$(readlink -f /etc/nginx/sites-enabled/default)
  fi
  if [ -f "$host_conf" ] && grep -q "# >>> SPAWNWP SITE ${name}\$" "$host_conf"; then
    sed -i "/# >>> SPAWNWP SITE ${name}\$/,/# <<< SPAWNWP SITE ${name}\$/ s/^\(\s*client_max_body_size\s\+\).*/\1${body};/" "$host_conf"
  fi
}
