#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

NAME="${1:-}"
if [ -z "$NAME" ]; then
  echo "Usage: $0 <project-name>" >&2
  exit 1
fi

PROJ_DIR="/srv/${NAME}"
NGINX_CONF="/etc/nginx/sites-available/default"

# ── Safety guards ────────────────────────────────────────────────────────────────
# Never destroy the primary stack.
if [ "$NAME" = "wp-dev" ]; then
  echo "ERROR: refusing to destroy the primary stack 'wp-dev'." >&2
  exit 1
fi
if [ ! -d "$PROJ_DIR" ]; then
  echo "ERROR: $PROJ_DIR does not exist." >&2
  exit 1
fi

cd "$PROJ_DIR"

# Containers must be down (the cockpit already checks, but we re-check).
RUNNING=$(docker compose ps -q --status running 2>/dev/null | grep -c . || true)
if [ "$RUNNING" != "0" ]; then
  echo "ERROR: $RUNNING containers are still running. Bring them 'Down' first." >&2
  exit 1
fi

echo "==> Removing leftover containers and volumes (DB + files) of '$NAME'..."
docker compose down -v --remove-orphans 2>&1 || true

echo "==> Removing the Nginx block for '$NAME'..."
python3 - <<PYEOF
conf_path = "${NGINX_CONF}"
name = "${NAME}"
with open(conf_path) as f:
    lines = f.readlines()

def remove_block(lines, start_pred, end_pred):
    """Delete from the first line matching start_pred up to (excluding) the first
    terminator matching end_pred; also drops one blank line before the start."""
    start = None
    for i, l in enumerate(lines):
        if start_pred(l.strip()):
            start = i
            break
    if start is None:
        return False
    end = len(lines)
    for k in range(start + 1, len(lines)):
        if end_pred(lines[k].strip()):
            end = k
            break
    head = start
    if head > 0 and lines[head - 1].strip() == "":
        head -= 1
    del lines[head:end]
    return True

def remove_marked(lines, start_marker, end_marker):
    start = next((i for i, line in enumerate(lines) if line.strip() == start_marker), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == end_marker), None)
    if end is None:
        raise RuntimeError(f"missing Nginx end marker: {end_marker}")
    head = start - 1 if start > 0 and lines[start - 1].strip() == "" else start
    del lines[head:end + 1]
    return True

wp = remove_marked(lines, f"# >>> SPAWNWP SITE {name}", f"# <<< SPAWNWP SITE {name}")
if not wp:
    # Compatibility with sites created before explicit markers. Start at the
    # Deploy route when present so it does not become orphaned.
    wp = remove_block(
        lines,
        lambda s: s.startswith(f"location /{name}/wp-json/spawnwp-deploy/v1/") or s.startswith(f"# ── {name} (port"),
        lambda s: s == "location @wp_down {" or (s.startswith("# ── ") and name not in s),
    )
admin = remove_marked(lines, f"# >>> SPAWNWP ADMIN {name}", f"# <<< SPAWNWP ADMIN {name}")
if not admin:
    admin = remove_block(
        lines,
        lambda s: s == f"# ── {name} admin ──",
        lambda s: s.startswith("# __COCKPIT_PER_SITE__") or s.startswith("# ── ") or s == "location / {",
    )
with open(conf_path, "w") as f:
    f.writelines(lines)
print(f"  -> Nginx blocks removed (site={wp}, admin={admin}).")
PYEOF

# NON-fatal Nginx steps: an error here must not prevent removing the directory
# (otherwise the container ends up destroyed but the dir orphaned — the bug that
# left /srv/patagarro behind). If 'nginx -t' fails we don't reload, but we still
# proceed with the deletion.
echo "  -> Testing Nginx config..."
if nginx -t 2>&1; then
  if systemctl reload nginx; then
    echo "  -> Nginx reloaded."
  else
    echo "  !! Nginx reload failed (block already removed from the file; run 'systemctl reload nginx' manually)." >&2
  fi
else
  echo "  !! 'nginx -t' reported errors: NOT reloading for safety. Check the config manually." >&2
fi

echo "==> Removing project directory ${PROJ_DIR}..."
cd /srv
rm -rf "$PROJ_DIR"

# Final check: the directory MUST be gone.
if [ -e "$PROJ_DIR" ]; then
  echo "❌ ERROR: ${PROJ_DIR} still exists after 'rm -rf'." >&2
  ls -la "$PROJ_DIR" >&2 || true
  exit 1
fi

echo ""
echo "==> Destroyed: site '$NAME' has been completely removed."
