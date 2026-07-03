#!/usr/bin/env bash
# Apply a schema v2 "content blueprint": extract the captured payload and
# replay its plugins/themes/uploads/database onto a freshly bootstrapped site.
# Invoked by apply-blueprint.sh; on failure the new-project.sh trap destroys
# the whole site, so no rollback is needed here.
set -euo pipefail
cd "$(dirname "$0")/.."
source .env

MANIFEST="${1:-.spawnwp/blueprint.json}"
if [ ! -f "$MANIFEST" ]; then
  echo "ERROR: resolved blueprint manifest not found: $MANIFEST" >&2
  exit 1
fi

eval "$(python3 - "$MANIFEST" <<'PY'
import json, shlex, sys
item = json.load(open(sys.argv[1], encoding="utf-8"))
capture = item["capture"]
values = {
    "BLUEPRINT_ID": item["id"], "BLUEPRINT_NAME": item["name"],
    "BLUEPRINT_VERSION": item["version"], "BLUEPRINT_THEME": item["theme"] or "",
    "BLUEPRINT_PAYLOAD": item["payload_path"],
    "BLUEPRINT_PAYLOAD_SHA256": item["payload"]["sha256"],
    "BLUEPRINT_WPORG_PLUGINS": " ".join(item["wporg_plugins"]),
    "CAPTURE_PLUGINS": "1" if capture["plugins"] else "0",
    "CAPTURE_THEMES": "1" if capture["themes"] else "0",
    "CAPTURE_UPLOADS": "1" if capture["uploads"] else "0",
    "CAPTURE_DATABASE": "1" if capture["database"] else "0",
    "BLUEPRINT_SOURCE": item["source"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"
WP=(docker compose exec -T -u www-data php wp)
PLACEHOLDER_URL="https://blueprint.spawnwp.invalid"
STAGE=".spawnwp/payload-stage"
IMPORT_DIR="projects/primary/wp-content/.spawnwp-import"
cleanup() { rm -rf "$STAGE" "$IMPORT_DIR"; }
trap cleanup EXIT

echo "==> Applying content blueprint ${BLUEPRINT_NAME} ${BLUEPRINT_VERSION}..."
echo "==> Verifying payload integrity..."
echo "${BLUEPRINT_PAYLOAD_SHA256}  ${BLUEPRINT_PAYLOAD}" | sha256sum -c --quiet

echo "==> Extracting payload..."
rm -rf "$STAGE"
mkdir -p "$STAGE"
# Hardened extraction: same rules the ingest already enforced, re-checked here
# as defense in depth (no absolute/../ paths, no symlinks, expected top level).
python3 - "$BLUEPRINT_PAYLOAD" "$STAGE" <<'PY'
import sys, zipfile
from pathlib import Path, PurePosixPath

archive, stage = sys.argv[1], Path(sys.argv[2])
with zipfile.ZipFile(archive) as bundle:
    for info in bundle.infolist():
        pure = PurePosixPath(info.filename)
        if pure.is_absolute() or ".." in pure.parts or "\\" in info.filename or "\x00" in info.filename:
            raise SystemExit(f"unsafe path in payload: {info.filename}")
        if pure.parts and pure.parts[0] not in {"database.jsonl", "content"}:
            raise SystemExit(f"unexpected top-level entry: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise SystemExit(f"symlink in payload: {info.filename}")
    bundle.extractall(stage)
PY

for kind in plugins themes uploads; do
  case "$kind" in
    plugins) enabled="$CAPTURE_PLUGINS" ;;
    themes) enabled="$CAPTURE_THEMES" ;;
    uploads) enabled="$CAPTURE_UPLOADS" ;;
  esac
  if [ "$enabled" = "1" ] && [ -d "$STAGE/content/$kind" ]; then
    echo "==> Installing captured ${kind}..."
    mkdir -p "projects/primary/wp-content/$kind"
    cp -a "$STAGE/content/$kind/." "projects/primary/wp-content/$kind/"
    chown -R 33:33 "projects/primary/wp-content/$kind"
  fi
done

"${WP[@]}" plugin delete akismet hello 2>&1 || true

if [ "$CAPTURE_DATABASE" = "1" ]; then
  echo "==> Importing captured database..."
  rm -rf "$IMPORT_DIR"
  mkdir -p "$IMPORT_DIR"
  mv "$STAGE/database.jsonl" "$IMPORT_DIR/database.jsonl"
  cp scripts/import-database.php "$IMPORT_DIR/import-database.php"
  chown -R 33:33 "$IMPORT_DIR"
  "${WP[@]}" eval-file /var/www/html/wp-content/.spawnwp-import/import-database.php \
    /var/www/html/wp-content/.spawnwp-import/database.jsonl "$WP_ADMIN_USER"
  rm -rf "$IMPORT_DIR"
  echo "==> Rewriting blueprint URLs to ${WP_HOME}..."
  # New site: rewrite guid too (--skip-columns= overrides the guid default).
  "${WP[@]}" search-replace "$PLACEHOLDER_URL" "$WP_HOME" \
    --all-tables-with-prefix --skip-columns= --report-changed-only >/dev/null
  if [ "$CAPTURE_PLUGINS" != "1" ] && [ -n "$BLUEPRINT_WPORG_PLUGINS" ]; then
    # The database lists them as active but their files were not captured;
    # premium plugins cannot be recovered this way (the user was warned).
    read -r -a plugins <<< "$BLUEPRINT_WPORG_PLUGINS"
    echo "==> Installing WordPress.org plugins referenced by the database: ${BLUEPRINT_WPORG_PLUGINS}"
    "${WP[@]}" plugin install "${plugins[@]}" || true
  fi
  if [ "$CAPTURE_THEMES" != "1" ] && [ -n "$BLUEPRINT_THEME" ]; then
    echo "==> Installing active theme referenced by the database: ${BLUEPRINT_THEME}"
    if ! "${WP[@]}" theme install "$BLUEPRINT_THEME"; then
      fallback=$("${WP[@]}" theme list --field=name | head -n 1 || true)
      echo "==> Theme unavailable on WordPress.org; activating ${fallback} instead."
      "${WP[@]}" theme activate "$fallback"
    fi
  fi
else
  if [ "$CAPTURE_PLUGINS" = "1" ]; then
    echo "==> Activating captured plugins..."
    "${WP[@]}" plugin activate --all 2>&1 || true
  elif [ -n "$BLUEPRINT_WPORG_PLUGINS" ]; then
    read -r -a plugins <<< "$BLUEPRINT_WPORG_PLUGINS"
    echo "==> Installing blueprint plugins: ${BLUEPRINT_WPORG_PLUGINS}"
    "${WP[@]}" plugin install "${plugins[@]}" --activate
  fi
  if [ -n "$BLUEPRINT_THEME" ]; then
    if [ "$CAPTURE_THEMES" = "1" ]; then
      echo "==> Activating captured theme: ${BLUEPRINT_THEME}"
      "${WP[@]}" theme activate "$BLUEPRINT_THEME"
    else
      echo "==> Installing blueprint theme: ${BLUEPRINT_THEME}"
      "${WP[@]}" theme install "$BLUEPRINT_THEME" --activate
    fi
  fi
fi

"${WP[@]}" option update spawnwp_blueprint_id "$BLUEPRINT_ID"
"${WP[@]}" option update spawnwp_blueprint_version "$BLUEPRINT_VERSION"
"${WP[@]}" option update spawnwp_blueprint_source "$BLUEPRINT_SOURCE"
"${WP[@]}" cache flush >/dev/null

echo "==> Content blueprint applied successfully."
