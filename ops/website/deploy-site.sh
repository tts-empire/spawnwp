#!/usr/bin/env bash
set -Eeuo pipefail

MODE=${1:---check}
if [[ "$MODE" != "--check" && "$MODE" != "--publish" ]]; then
  echo "usage: $0 [--check|--publish]" >&2
  exit 2
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASES_DIR=${SPAWNWP_WEBSITE_RELEASES:-/var/www/spawnwp.com/releases}
PUBLIC_LINK=${SPAWNWP_WEBSITE_PUBLIC:-/var/www/spawnwp.com/public}
VENV_DIR=${SPAWNWP_WEBSITE_VENV:-/var/lib/spawnwp-website/venv}
LOCK_FILE=${SPAWNWP_WEBSITE_LOCK:-/var/lock/spawnwp-website-deploy.lock}
HOST=${SPAWNWP_WEBSITE_HOST:-spawnwp.com}
PUBLISH=0
[[ "$MODE" == "--publish" ]] && PUBLISH=1

for command in git python3 rsync curl sha256sum; do
  command -v "$command" >/dev/null || { echo "missing command: $command" >&2; exit 1; }
done

cd "$ROOT"

if (( PUBLISH )); then
  [[ -z "$(git status --porcelain)" ]] || { echo "refusing a dirty worktree" >&2; exit 1; }
  [[ $EUID -eq 0 ]] || { echo "--publish must run as root" >&2; exit 1; }
  command -v nginx >/dev/null || { echo "missing command: nginx" >&2; exit 1; }
  command -v gh >/dev/null || { echo "missing command: gh" >&2; exit 1; }
  [[ "$(git branch --show-current)" == "main" ]] || { echo "publish requires branch main" >&2; exit 1; }
  git fetch --quiet origin main
  [[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || {
    echo "publish requires HEAD to match origin/main" >&2
    exit 1
  }
  exec 9>"$LOCK_FILE"
  flock -n 9 || { echo "another website deploy is running" >&2; exit 1; }
  head_sha=$(git rev-parse HEAD)
  for workflow in test site; do
    conclusion=$(gh run list --repo tts-empire/spawnwp --workflow "$workflow" --commit "$head_sha" --limit 1 --json conclusion --jq '.[0].conclusion // ""')
    [[ "$conclusion" == "success" ]] || {
      echo "workflow $workflow is not successful for $head_sha" >&2
      exit 1
    }
  done
fi

requirements_hash=$(sha256sum website/requirements.txt | awk '{print $1}')
stamp="$VENV_DIR/.requirements.sha256"
if [[ ! -x "$VENV_DIR/bin/mkdocs" || ! -f "$stamp" || "$(cat "$stamp")" != "$requirements_hash" ]]; then
  mkdir -p "$(dirname "$VENV_DIR")"
  python3 -m venv "$VENV_DIR"
  "$VENV_DIR/bin/pip" install --disable-pip-version-check -r website/requirements.txt
  printf '%s\n' "$requirements_hash" > "$stamp"
fi

if (( PUBLISH )); then
  mkdir -p "$RELEASES_DIR"
  stage=$(mktemp -d "$RELEASES_DIR/.website-build.XXXXXX")
else
  stage=$(mktemp -d /tmp/spawnwp-website-check.XXXXXX)
fi
previous=$(readlink -f "$PUBLIC_LINK" 2>/dev/null || true)
flipped=0
final=""

cleanup() {
  status=$?
  if (( status != 0 && flipped )) && [[ -n "$previous" ]]; then
    rollback_link="${PUBLIC_LINK}.rollback.$$"
    ln -s "$previous" "$rollback_link"
    mv -Tf "$rollback_link" "$PUBLIC_LINK"
    echo "deploy failed; restored $previous" >&2
  fi
  if [[ -d "$stage" ]]; then rm -rf "$stage"; fi
  exit "$status"
}
trap cleanup EXIT

rsync -a --exclude=docs --exclude=sitemap-pages.xml website/ "$stage/"
install -m 0644 install.sh "$stage/install.sh"
if (( PUBLISH )) && [[ -n "$previous" && -d "$previous/artifacts" ]]; then
  cp -a "$previous/artifacts" "$stage/artifacts"
fi

python3 ops/website/generate-sitemap.py --root website --repo "$ROOT" --output "$stage/sitemap-pages.xml"
"$VENV_DIR/bin/mkdocs" build --strict -f website/mkdocs-public.yml -d "$stage/docs"
python3 ops/website/check-site.py --root "$stage" --sitemap "$stage/sitemap-pages.xml"

if (( ! PUBLISH )); then
  echo "website check passed in $stage"
  exit 0
fi

nginx -t
max=0
while IFS= read -r name; do
  number=${name##*website-v1.}
  [[ "$number" =~ ^[0-9]+$ ]] && (( number > max )) && max=$number
done < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%f\n')
release="$(date +%Y%m%d)-website-v1.$((max + 1))"
final="$RELEASES_DIR/$release"
[[ ! -e "$final" ]] || { echo "release exists: $final" >&2; exit 1; }
chmod -R a+rX "$stage"
mv "$stage" "$final"
stage=""

next_link="${PUBLIC_LINK}.next.$$"
ln -s "$final" "$next_link"
mv -Tf "$next_link" "$PUBLIC_LINK"
flipped=1

base="https://$HOST"
for path in / /wordpress-sandbox/ /alternatives/ /alternatives/instawp/ /alternatives/localwp/ /alternatives/tastewp/ /use-cases/ /use-cases/plugin-development/ /guides/ /guides/test-wordpress-multiple-php-versions/ /guides/wordpress-sandbox-vs-staging/ /docs/ /sitemap.xml /sitemap-pages.xml; do
  curl --resolve "$HOST:443:127.0.0.1" -fsS -o /dev/null "$base$path"
done

live_hash=$(curl --resolve "$HOST:443:127.0.0.1" -fsS "$base/install.sh" | sha256sum | awk '{print $1}')
repo_hash=$(sha256sum install.sh | awk '{print $1}')
[[ "$live_hash" == "$repo_hash" ]] || { echo "live install.sh checksum mismatch" >&2; exit 1; }
bash ops/website/check-live-install.sh
python3 ops/website/sync_wporg_plugin.py --check

flipped=0
echo "published $release (previous: ${previous:-none})"
