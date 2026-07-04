#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
VERSION=${1:-$(<"$ROOT/VERSION")}
KEY=${SPAWNWP_RELEASE_KEY:-/root/.config/spawnwp/release-private.pem}
TAG="v${VERSION}"

command -v gh >/dev/null || { echo "ERROR: GitHub CLI is required." >&2; exit 1; }
gh auth status >/dev/null

# Guard: the package is built from the working tree, so the tree must be exactly
# the tagged commit. Otherwise a checkout that has moved past the tag would get
# published under the older version number.
git -C "$ROOT" rev-parse -q --verify "refs/tags/$TAG" >/dev/null || {
  echo "ERROR: tag $TAG does not exist locally; tag the release commit first." >&2
  exit 1
}
if [ "$(git -C "$ROOT" rev-parse HEAD)" != "$(git -C "$ROOT" rev-parse "$TAG^{commit}")" ]; then
  echo "ERROR: HEAD is not at tag $TAG. Build from the tag instead:" >&2
  echo "  git worktree add /tmp/spawnwp-$TAG $TAG && bash /tmp/spawnwp-$TAG/updater/publish-release.sh $VERSION" >&2
  exit 1
fi
if [ -n "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ]; then
  echo "ERROR: the working tree has uncommitted changes; commit or stash them first." >&2
  exit 1
fi

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
