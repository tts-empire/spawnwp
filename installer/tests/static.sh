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
