#!/usr/bin/env bash
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
values = {
    "BLUEPRINT_ID": item["id"], "BLUEPRINT_NAME": item["name"],
    "BLUEPRINT_VERSION": item["version"], "BLUEPRINT_PLUGINS": " ".join(item["plugins"]),
    "BLUEPRINT_THEME": item["theme"] or "", "BLUEPRINT_DEVKIT": "1" if item["devkit"] else "0",
    "BLUEPRINT_CONTENT": item["content_preset"],
}
for key, value in values.items():
    print(f"{key}={shlex.quote(value)}")
PY
)"
WP=(docker compose exec -T -u www-data php wp)

echo "==> Applying blueprint ${BLUEPRINT_NAME} ${BLUEPRINT_VERSION}..."
"${WP[@]}" plugin delete akismet hello 2>&1 || true
debug_raw=$(python3 -c 'import json,sys; print("true" if json.load(open(sys.argv[1]))["debug"] else "false")' "$MANIFEST")
"${WP[@]}" config set WP_DEBUG "$debug_raw" --raw
"${WP[@]}" config set WP_DEBUG_LOG "$debug_raw" --raw

content_ids=$("${WP[@]}" post list --post_type=post,page --format=ids)
if [ -n "$content_ids" ]; then
  # IDs are emitted by WP-CLI itself, not supplied by the manifest.
  read -r -a ids <<< "$content_ids"
  "${WP[@]}" post delete "${ids[@]}" --force >/dev/null
fi

if [ -n "$BLUEPRINT_PLUGINS" ]; then
  read -r -a plugins <<< "$BLUEPRINT_PLUGINS"
  echo "==> Installing blueprint plugins: ${BLUEPRINT_PLUGINS}"
  "${WP[@]}" plugin install "${plugins[@]}" --activate
fi

if [ -n "$BLUEPRINT_THEME" ]; then
  echo "==> Installing blueprint theme: ${BLUEPRINT_THEME}"
  "${WP[@]}" theme install "$BLUEPRINT_THEME" --activate
fi

if [ "$BLUEPRINT_DEVKIT" = "1" ]; then
  echo "==> Installing SpawnWP development toolkit..."
  install -D -o 33 -g 33 -m 0644 scripts/devkit.php projects/primary/wp-content/mu-plugins/devkit.php
else
  rm -f projects/primary/wp-content/mu-plugins/devkit.php
fi

if [ "$BLUEPRINT_CONTENT" = "demo" ]; then
  echo "==> Creating demo pages and navigation..."
  home_id=$("${WP[@]}" post create --post_type=page --post_status=publish --post_title="Home" --post_content="Welcome to your new WordPress site. Replace this page with your own introduction, services and primary call to action." --porcelain)
  about_id=$("${WP[@]}" post create --post_type=page --post_status=publish --post_title="About" --post_content="Use this page to explain who you are, what you do and why your work matters." --porcelain)
  contact_id=$("${WP[@]}" post create --post_type=page --post_status=publish --post_title="Contact" --post_content="Add your preferred contact details or contact form here." --porcelain)
  "${WP[@]}" option update show_on_front page
  "${WP[@]}" option update page_on_front "$home_id"
  "${WP[@]}" menu create "Primary" >/dev/null
  "${WP[@]}" menu item add-post Primary "$home_id" >/dev/null
  "${WP[@]}" menu item add-post Primary "$about_id" >/dev/null
  "${WP[@]}" menu item add-post Primary "$contact_id" >/dev/null
  first_location=$("${WP[@]}" menu location list --fields=location --format=csv 2>/dev/null | tail -n +2 | head -n 1 || true)
  if [ -n "$first_location" ]; then
    "${WP[@]}" menu location assign Primary "$first_location" >/dev/null
  fi
fi

"${WP[@]}" option update spawnwp_blueprint_id "$BLUEPRINT_ID"
"${WP[@]}" option update spawnwp_blueprint_version "$BLUEPRINT_VERSION"
"${WP[@]}" option update spawnwp_blueprint_source "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source"])' "$MANIFEST")"
"${WP[@]}" cache flush >/dev/null

echo "==> Blueprint applied successfully."
