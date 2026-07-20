#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

[[ $EUID -eq 0 ]] || { echo "install-plugin-sync.sh must run as root" >&2; exit 1; }

install -d -m 0755 /usr/local/lib/spawnwp
install -d -m 0755 /var/www/spawnwp-downloads
install -d -m 0700 /var/backups/spawnwp-plugin-previews
install -m 0755 "$ROOT/ops/website/sync_wporg_plugin.py" \
  /usr/local/lib/spawnwp/sync_wporg_plugin.py
install -m 0644 "$ROOT/ops/website/systemd/spawnwp-plugin-sync.service" \
  /etc/systemd/system/spawnwp-plugin-sync.service
install -m 0644 "$ROOT/ops/website/systemd/spawnwp-plugin-sync.timer" \
  /etc/systemd/system/spawnwp-plugin-sync.timer

systemctl daemon-reload
systemctl enable --now spawnwp-plugin-sync.timer
if ! systemctl start spawnwp-plugin-sync.service; then
  systemctl --no-pager --lines=20 status spawnwp-plugin-sync.service || true
  exit 1
fi
systemctl --no-pager --lines=8 status spawnwp-plugin-sync.service || true
