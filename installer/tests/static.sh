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
grep -q 'First use of PHP' "$ROOT/runtime/scripts/php-switch-progress.py"
grep -q 'Show technical details' "$ROOT/runtime/assets/cockpit.js"
# Manage dashboard: resilient refresh + collapse + filter (0.5.14).
grep -q 'function filterProjects' "$ROOT/runtime/assets/cockpit.js"
grep -q 'function toggleCollapse' "$ROOT/runtime/assets/cockpit.js"
grep -q 'const DESTROYING = new Set' "$ROOT/runtime/assets/cockpit.js"
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
