#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=${1:-$(<"$ROOT/VERSION")}
KEY=${SPAWNWP_RELEASE_KEY:-/root/.config/spawnwp/release-private.pem}
TAG="v${VERSION}"

command -v gh >/dev/null || { echo "ERROR: GitHub CLI is required." >&2; exit 1; }
gh auth status >/dev/null
python3 "$ROOT/updater/build-release.py" --version "$VERSION" --key "$KEY"

if gh release view "$TAG" --repo tts-empire/spawnwp >/dev/null 2>&1; then
  echo "ERROR: Release $TAG already exists; published releases are immutable." >&2
  exit 1
fi

gh release create "$TAG" \
  "$ROOT/dist/spawnwp-$VERSION.tar.gz" \
  "$ROOT/dist/spawnwp-$VERSION.manifest.json" \
  "$ROOT/dist/spawnwp-$VERSION.manifest.sig" \
  "$ROOT/dist/spawnwp-$VERSION.sha256" \
  --repo tts-empire/spawnwp \
  --title "SpawnWP $VERSION" \
  --notes-file "$ROOT/docs/release-notes/$VERSION.md" \
  --verify-tag

# Non-fatal: remind the operator if the install.sh served by spawnwp.com is stale.
bash "$ROOT/ops/website/check-live-install.sh" || true
