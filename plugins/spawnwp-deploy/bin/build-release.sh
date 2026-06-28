#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
KEY=${1:-}

if [ -z "$KEY" ] || [ ! -f "$KEY" ]; then
  echo "usage: $0 /secure/path/to/ed25519-private.pem" >&2
  exit 1
fi

VERSION=$(sed -n 's/^ \* Version: //p' "$ROOT/spawnwp-deploy.php" | head -1)
DIST="$ROOT/dist"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$DIST" "$STAGE/spawnwp-deploy"
cp -a "$ROOT/spawnwp-deploy.php" "$ROOT/src" "$ROOT/recovery" "$ROOT/README.md" "$STAGE/spawnwp-deploy/"

ZIP="$DIST/spawnwp-deploy-${VERSION}.zip"
rm -f "$ZIP" "$ZIP.sha256" "$ZIP.sig"
(cd "$STAGE" && python3 -m zipfile -c "$ZIP" spawnwp-deploy)
(
  cd "$DIST"
  sha256sum "$(basename "$ZIP")" > "$(basename "$ZIP.sha256")"
)
openssl pkeyutl -sign -rawin -inkey "$KEY" -in "$ZIP.sha256" | base64 -w0 > "$ZIP.sig"
printf '\n' >> "$ZIP.sig"

echo "Built: $ZIP"
echo "SHA-256: $(cut -d' ' -f1 "$ZIP.sha256")"
