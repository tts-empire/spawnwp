#!/usr/bin/env python3
"""Add the validated official-module include to an existing cockpit vhost."""

import os
import re
import subprocess
from pathlib import Path

_explicit_nginx = os.environ.get("SPAWNWP_NGINX_CONF")
NGINX_CONF = Path(_explicit_nginx) if _explicit_nginx else None
SITES_ENABLED = Path(os.environ.get("SPAWNWP_NGINX_SITES_ENABLED", "/etc/nginx/sites-enabled"))
SPAWNWP_CONFIG = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
MODULE_DIR = Path(os.environ.get("SPAWNWP_NGINX_MODULE_DIR", "/etc/nginx/spawnwp-modules"))
INCLUDE = "    include /etc/nginx/spawnwp-modules/*.conf;\n"
ANCHOR = "    location /assets/ {\n"


def cockpit_domain() -> str:
    try:
        for line in SPAWNWP_CONFIG.read_text().splitlines():
            if line.startswith("COCKPIT_DOMAIN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def nginx_conf() -> Path | None:
    if NGINX_CONF is not None:
        return NGINX_CONF
    domain = cockpit_domain()
    try:
        enabled = sorted(SITES_ENABLED.iterdir())
    except OSError:
        enabled = []
    for entry in enabled:
        try:
            candidate = entry.resolve(strict=True)
            content = candidate.read_text()
        except OSError:
            continue
        if ANCHOR not in content:
            continue
        if domain and not re.search(rf"\bserver_name\s+[^;]*\b{re.escape(domain)}\b", content):
            continue
        return candidate
    for candidate in (
        Path("/etc/nginx/sites-available/spawnwp"),
        Path("/etc/nginx/sites-available/spawnwp.com"),
    ):
        if candidate.is_file() and ANCHOR in candidate.read_text():
            return candidate
    return None


def main() -> int:
    MODULE_DIR.mkdir(parents=True, exist_ok=True)
    config = nginx_conf()
    if config is None or not config.is_file():
        return 0
    original = config.read_text()
    if INCLUDE.strip() in original:
        return 0
    if ANCHOR not in original:
        raise SystemExit("cockpit assets location anchor not found")
    config.write_text(original.replace(ANCHOR, INCLUDE + ANCHOR, 1))
    try:
        check = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
        if check.returncode != 0:
            raise RuntimeError(check.stderr.strip() or "nginx validation failed")
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    except Exception as exc:
        config.write_text(original)
        subprocess.run(
            ["systemctl", "reload", "nginx"], check=False,
            capture_output=True, text=True,
        )
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
