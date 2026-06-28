#!/usr/bin/env python3
"""Remove the obsolete source-IP network gate from existing installations."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))
CONFIG_ENV = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
FEATURES = Path(os.environ.get("SPAWNWP_FEATURES", "/var/lib/spawnwp/features.json"))
REPORT = Path(os.environ.get("SPAWNWP_REPORT", "/root/spawnwp-credentials.txt"))


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def remove_nginx_gate() -> None:
    if not NGINX_CONF.is_file():
        return
    original = NGINX_CONF.read_text()
    updated = re.sub(
        r"^\s*include /etc/nginx/cockpit-allowed\.conf;\s*\n",
        "",
        original,
        flags=re.MULTILINE,
    )
    if updated == original:
        return
    NGINX_CONF.write_text(updated)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        NGINX_CONF.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def clean_metadata() -> None:
    if CONFIG_ENV.is_file():
        lines = [
            line for line in CONFIG_ENV.read_text().splitlines()
            if not line.startswith("ENABLE_PORT_KNOCKING=")
        ]
        CONFIG_ENV.write_text("\n".join(lines) + "\n")
        os.chmod(CONFIG_ENV, 0o600)
    if FEATURES.is_file():
        try:
            features = json.loads(FEATURES.read_text())
        except json.JSONDecodeError:
            features = {}
        features.pop("port_knocking", None)
        FEATURES.write_text(json.dumps(features, separators=(",", ":")) + "\n")
    if REPORT.is_file():
        report = re.sub(
            r"\nPort-knocking:.*?(?=\n\nThis root-only report is stored at:)",
            "",
            REPORT.read_text(),
            flags=re.DOTALL,
        )
        REPORT.write_text(report.rstrip() + "\n")
        os.chmod(REPORT, 0o600)


def remove_services_and_files() -> None:
    run(["systemctl", "disable", "--now", "knockd", "cockpit-reaper.timer"], check=False)
    for path in (
        "/etc/systemd/system/cockpit-reaper.service",
        "/etc/systemd/system/cockpit-reaper.timer",
        "/usr/local/lib/spawnwp/knock-session",
        "/etc/nginx/cockpit-allowed.conf",
        "/etc/knockd.conf",
        "/etc/default/knockd",
    ):
        Path(path).unlink(missing_ok=True)
    for path in ("/run/cockpit-sessions", "/run/lock/spawnwp-knock.lock"):
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"], check=False)
    run(["apt-get", "purge", "-y", "knockd"], check=False)


def main() -> int:
    remove_nginx_gate()
    clean_metadata()
    remove_services_and_files()
    run(["nginx", "-t"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
