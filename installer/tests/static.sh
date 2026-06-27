#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
bash -n "$ROOT/install.sh"
python3 -m py_compile "$ROOT/installer/knock-session" "$ROOT/installer/telemetry.py"
if grep -RIE --exclude-dir=tests 'presenzaweb|maurizio\.savoni' "$ROOT/runtime" "$ROOT/installer" "$ROOT/install.sh"; then
  echo "ERROR: deployment-specific value found" >&2
  exit 1
fi
grep -q 'Enable port-knocking' "$ROOT/install.sh"
grep -q 'Share anonymous usage statistics for 90 days' "$ROOT/install.sh"
grep -q 'pkeyutl -verify' "$ROOT/install.sh"
