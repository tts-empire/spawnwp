#!/usr/bin/env bash
# Verify that the install.sh served by spawnwp.com matches the repo copy.
# The website deploy is a manual release-dir flip and install.sh is easy to
# forget: this check is run (non-fatally) at the end of publish-release.sh.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
URL=${SPAWNWP_INSTALL_URL:-https://spawnwp.com/install.sh}

live=$(mktemp)
trap 'rm -f "$live"' EXIT
curl -fsS --max-time 10 -o "$live" "$URL" || {
  echo "WARNING: could not fetch $URL to compare install.sh." >&2
  exit 2
}
if diff -q "$live" "$ROOT/install.sh" >/dev/null; then
  echo "OK: $URL matches the repo install.sh."
else
  echo "WARNING: $URL differs from the repo install.sh." >&2
  echo "Update the live copy in the current website release dir, e.g.:" >&2
  echo "  install -m 644 $ROOT/install.sh /var/www/spawnwp.com/public/install.sh" >&2
  exit 1
fi
