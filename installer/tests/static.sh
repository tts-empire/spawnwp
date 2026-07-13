#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
bash -n "$ROOT/install.sh"
python3 -m py_compile "$ROOT/installer/telemetry.py"
if grep -RIE --exclude-dir=tests 'presenzaweb|maurizio\.savoni' "$ROOT/runtime" "$ROOT/installer" "$ROOT/install.sh"; then
  echo "ERROR: deployment-specific value found" >&2
  exit 1
fi
if grep -qE 'auth_basic|htpasswd' "$ROOT/install.sh" "$ROOT/installer/nginx.conf.tpl"; then
  echo "ERROR: legacy HTTP Basic Auth found" >&2
  exit 1
fi
if grep -RIE 'port[-_ ]?knock|knockd|knock-session|cockpit-reaper' "$ROOT/install.sh" "$ROOT/runtime" "$ROOT/installer/nginx.conf.tpl"; then
  echo "ERROR: obsolete network gate found" >&2
  exit 1
fi
grep -q 'Share anonymous usage statistics for 90 days' "$ROOT/install.sh"
grep -q 'telemetry.py enable' "$ROOT/install.sh"
grep -q 'COCKPIT FIRST-TIME ACTIVATION' "$ROOT/install.sh"
grep -q 'Valid for 24 hours and usable once' "$ROOT/install.sh"
grep -q 'sudo cat \$REPORT' "$ROOT/install.sh"
grep -q 'pkeyutl -verify' "$ROOT/install.sh"
grep -q 'template-only' "$ROOT/install.sh"
if grep -Eq 'docker compose (build|up)|make bootstrap|apply-blueprint\.sh' "$ROOT/install.sh"; then
  echo "installer must not create or bootstrap a WordPress environment" >&2
  exit 1
fi
grep -q 'cp env.example "${PROJ_DIR}/.env.example"' "$ROOT/runtime/scripts/new-project.sh"
grep -q 'Spaces are not allowed in site URLs' "$ROOT/runtime/assets/cockpit.js"
grep -q 'DOCKER_CONFIG=/var/lib/spawnwp/docker' "$ROOT/installer/wp-cockpit.service"
grep -q 'ExecStart=/usr/local/bin/spawnwp update' "$ROOT/installer/spawnwp-update.service"
grep -q 'Install update' "$ROOT/runtime/updates.html"
grep -q '/auth/reauth/start' "$ROOT/runtime/assets/cockpit.js"
grep -q "renderGroup('SpawnWP blueprints', builtIn)" "$ROOT/runtime/assets/cockpit.js"
grep -q "renderGroup('Your blueprints', captured)" "$ROOT/runtime/assets/cockpit.js"
grep -q "renderGroup('Custom manifests', customManifests)" "$ROOT/runtime/assets/cockpit.js"
if grep -q 'badge badge-yellow">Template' "$ROOT/runtime/assets/cockpit.js"; then
  echo "captured blueprints must not use the ambiguous Template badge" >&2
  exit 1
fi
grep -q 'class="section system-panel"' "$ROOT/runtime/system.html"
grep -q 'class="btn-primary sensitive" type="button" id="bp-pair-generate"' "$ROOT/runtime/system.html"
grep -q '#reauth-dialog { position:fixed; inset:0;' "$ROOT/runtime/assets/cockpit.css"
grep -q 'prefers-reduced-motion: reduce' "$ROOT/runtime/assets/cockpit.css"
grep -q 'input,button{width:100%;min-height:44px' "$ROOT/runtime/auth.py"
grep -q 'input:focus-visible,button:focus-visible' "$ROOT/runtime/auth.py"
grep -q 'System → Blueprint capture' "$ROOT/runtime/assets/cockpit.js"
grep -q 'UPDATE sessions SET recent_auth' "$ROOT/runtime/auth.py"
grep -q 'php-switch-progress.py' "$ROOT/updater/managed-files.json"
# PHP image (0.5.18): FTP/FTPS and the extensions sites depend on. FTPS needs the
# OpenSSL headers plus an explicit opt-in whose flag was renamed in PHP 8.4 —
# a refactor that drops any of these silently breaks FTPS again.
grep -q 'libssl-dev' "$ROOT/runtime/docker/php/Dockerfile"
grep -q -- '--with-ftp-ssl' "$ROOT/runtime/docker/php/Dockerfile"
grep -q -- '--with-openssl-dir' "$ROOT/runtime/docker/php/Dockerfile"
grep -q 'pdo_mysql' "$ROOT/runtime/docker/php/Dockerfile"

# Every extension the image installs must be documented. A hand-kept list rots the
# first time someone adds an extension and forgets the page — and a docs page that
# lies is worse than no page. Adding an extension without documenting it fails CI.
# Sources: the docker-php-ext-install list, the standalone `docker-php-ext-install gd`,
# and the PECL line (imagick, redis).
DOCKERFILE="$ROOT/runtime/docker/php/Dockerfile"
EXT_DOC="$ROOT/docs/wordpress-development.md"
# Strip comments first (they mention 'docker-php-ext-install' in prose), then join
# backslash-continued lines so a multi-line install list reads as one command.
DOCKERFILE_FLAT=$(sed 's/#.*$//' "$DOCKERFILE" | sed -e :a -e '/\\$/N; s/\\\n/ /; ta')
IMAGE_EXTS=$(
  {
    grep -oE 'docker-php-ext-install [a-z_0-9 ]+' <<<"$DOCKERFILE_FLAT" | sed 's/docker-php-ext-install //'
    grep -oE 'pecl install [a-z_0-9 ]+'           <<<"$DOCKERFILE_FLAT" | sed 's/pecl install //'
  } | tr ' ' '\n' | sort -u | grep -vE '^$'
)
if [ -z "$IMAGE_EXTS" ]; then
  echo "static.sh could not parse any extension out of the php Dockerfile — fix the parser" >&2
  exit 1
fi
# Search only the extensions table, not the whole page: a name mentioned in the
# "Not available" section (e.g. imap) must not count as documenting an extension we
# actually ship — that would let the page claim the opposite of the truth.
EXT_TABLE=$(sed -n '/^## PHP extensions/,/^### Not available/p' "$EXT_DOC")
if [ -z "$EXT_TABLE" ]; then
  echo "docs/wordpress-development.md is missing the '## PHP extensions' section" >&2
  exit 1
fi
for ext in $IMAGE_EXTS; do
  if ! grep -qw "$ext" <<<"$EXT_TABLE"; then
    echo "PHP extension '$ext' is installed in the image but not documented in the extensions table of docs/wordpress-development.md" >&2
    exit 1
  fi
done
# ── PHP image identity (0.5.20) ───────────────────────────────────────────────
# The image tag must carry the WordPress version, because the image CONTENT does
# (the Dockerfile bakes WORDPRESS_VERSION in). Keyed on PHP alone, a `latest` site
# and one pinning a WP version re-tagged a single shared name from under each
# other and rebuilt forever — and a recreated site could come back up on another
# site's WordPress core.
LIB_IMAGE="$ROOT/runtime/scripts/lib-image.sh"
grep -q 'spawnwp_image_tag'    "$LIB_IMAGE"
grep -q 'spawnwp_context_hash' "$LIB_IMAGE"
grep -q 'WP_IMAGE_SUFFIX' "$ROOT/runtime/compose.yaml"
grep -q 'image: wp-dev-php:${PHP_VERSION:-8.3}${WP_IMAGE_SUFFIX:-}' "$ROOT/runtime/compose.yaml"
grep -q 'WP_IMAGE_SUFFIX' "$ROOT/runtime/scripts/new-project.sh"
grep -q 'IMAGE_TAG_RE' "$ROOT/runtime/app.py"
grep -q '_image_tags_in_use'  "$ROOT/runtime/app.py"

# The context hash must exist in exactly ONE place. It used to be copy-pasted into
# two scripts and MISSING from a third (php-switch), which built with
# SPAWNWP_CONTEXT_HASH unset — compose then stamped the label "dev" and every later
# deploy on that PHP version rebuilt from scratch. Re-introducing a second copy is
# the bug, so fail on it.
if grep -RIn -- 'find \. -type f' "$ROOT/runtime/scripts/" | grep -qv 'lib-image.sh'; then
  echo "the php build-context hash must only be computed in runtime/scripts/lib-image.sh" >&2
  grep -RIn -- 'find \. -type f' "$ROOT/runtime/scripts/" | grep -v 'lib-image.sh' >&2
  exit 1
fi
grep -q 'SPAWNWP_CONTEXT_HASH' "$ROOT/runtime/scripts/php-switch-progress.py" || {
  echo "php-switch must stamp SPAWNWP_CONTEXT_HASH, or its build poisons the image label with 'dev'" >&2
  exit 1
}

# Every build input the Dockerfile COPYs must be in lib-image.sh's manifest.
# Miss one and a change to it silently stops busting the cache: sites would keep
# running a stale image with no way to tell. (zz-site.ini is excluded on purpose:
# it is a per-site runtime mount, not a build input.)
IMAGE_INPUTS=$(sed -n 's/^SPAWNWP_IMAGE_INPUTS=(\(.*\))$/\1/p' "$LIB_IMAGE")
if [ -z "$IMAGE_INPUTS" ]; then
  echo "static.sh could not parse SPAWNWP_IMAGE_INPUTS out of lib-image.sh — fix the parser" >&2
  exit 1
fi
while read -r copied; do
  [ -n "$copied" ] || continue
  [ "$copied" = "zz-site.ini" ] && continue
  if ! grep -qw -- "$copied" <<<"$IMAGE_INPUTS"; then
    echo "the php Dockerfile COPYs '$copied' but it is not in SPAWNWP_IMAGE_INPUTS (runtime/scripts/lib-image.sh):" >&2
    echo "  a change to it would not rebuild the image, and sites would silently keep a stale one" >&2
    exit 1
  fi
done < <(grep -E '^COPY ' "$DOCKERFILE" | awk '{print $2}')

# Deploys must not race the first-run WordPress extraction: the php healthcheck is
# `php-fpm -t` (a config test) and passes long before the volume is populated, so
# bootstrap died with "This does not seem to be a WordPress installation".
grep -q 'wait-for-wordpress.sh' "$ROOT/runtime/scripts/bootstrap.sh"
grep -q 'wp core version' "$ROOT/runtime/scripts/wait-for-wordpress.sh"

# HTTP/2 on the TLS vhosts (nginx is already built --with-http_v2_module).
if [ "$(grep -c 'listen \(\[::\]:\)\?443 ssl http2;' "$ROOT/installer/nginx.conf.tpl")" != "4" ]; then
  echo "installer/nginx.conf.tpl must enable http2 on all four TLS listen lines (site + cockpit, v4 + v6)" >&2
  exit 1
fi

grep -q 'First use of PHP' "$ROOT/runtime/scripts/php-switch-progress.py"
grep -q 'Show technical details' "$ROOT/runtime/assets/cockpit.js"
# Manage dashboard: resilient refresh + collapse + filter (0.5.14).
grep -q 'function filterProjects' "$ROOT/runtime/assets/cockpit.js"
grep -q 'function toggleCollapse' "$ROOT/runtime/assets/cockpit.js"
grep -q 'function collapseKey' "$ROOT/runtime/assets/cockpit.js"
grep -q 'card-title" role="button"' "$ROOT/runtime/assets/cockpit.js"
grep -q 'const DESTROYING = new Set' "$ROOT/runtime/assets/cockpit.js"
# Site grouping (0.5.16): label stored per site in its .env, never interpolated
# into an inline handler (a hand-edited .env could carry quotes).
grep -q 'function layoutProjects' "$ROOT/runtime/assets/cockpit.js"
grep -q 'function toggleGroupFromEl' "$ROOT/runtime/assets/cockpit.js"
grep -q 'onclick="toggleGroupFromEl(this)"' "$ROOT/runtime/assets/cockpit.js"
grep -q 'SPAWNWP_GROUP' "$ROOT/runtime/scripts/new-project.sh"
grep -q '@app.post("/api/group/{project}")' "$ROOT/runtime/app.py"
grep -q 'GROUP_RE' "$ROOT/runtime/app.py"
grep -q 'id="sites-groupby"' "$ROOT/runtime/manage.html"
grep -q 'id="new-group"' "$ROOT/runtime/deploy.html"
# 0.5.17: the group chip IS the editor. The group flow must never open the site's
# log console again (its "output" header and copy icon are meaningless in a form).
grep -q 'function editGroup' "$ROOT/runtime/assets/cockpit.js"
grep -q 'const EDITING_GROUP = new Set' "$ROOT/runtime/assets/cockpit.js"
grep -q '/group-colors' "$ROOT/runtime/assets/cockpit.js"   # built as ${BASE}/group-colors
grep -q '@app.post("/api/group-colors")' "$ROOT/runtime/app.py"
grep -q 'GROUP_COLORS_FILE' "$ROOT/runtime/app.py"
if grep -q 'function showGroup' "$ROOT/runtime/assets/cockpit.js"; then
  echo "the group editor must not reuse the output box (showGroup was removed in 0.5.17)" >&2
  exit 1
fi
grep -q '.card.collapsed .actions' "$ROOT/runtime/assets/cockpit.css"
grep -q 'oninput="filterProjects(this.value)"' "$ROOT/runtime/manage.html"
if grep -RIE 'cockpit-allowed\.conf' "$ROOT/runtime" "$ROOT/install.sh" "$ROOT/installer/nginx.conf.tpl"; then
  echo "active runtime must not reference the removed cockpit network allow-list" >&2
  exit 1
fi

# Asset cache-busting must match the release: a stale ?v= serves users the old
# cockpit.js/css from browser cache (the 0.3.14 System-tab "Loading" bug).
VERSION_STR=$(tr -d '[:space:]' < "$ROOT/VERSION")
for page in manage deploy updates system; do
  if grep -Eo '\?v=[0-9a-zA-Z.]+' "$ROOT/runtime/${page}.html" | grep -qv "?v=${VERSION_STR}$"; then
    echo "runtime/${page}.html has an asset ?v= that does not match VERSION (${VERSION_STR})" >&2
    exit 1
  fi
done
