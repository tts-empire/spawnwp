#!/usr/bin/env python3
"""Add the dedicated provisioning rate limit and timeout to existing hosts."""

import os
import re
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))
PROXY_SNIPPET = Path(os.environ.get("SPAWNWP_NGINX_SNIPPET", "/etc/nginx/snippets/spawnwp-proxy.conf"))

ZONE_LINE = "limit_req_zone $binary_remote_addr zone=spawnwp_provision:10m rate=12r/m;\n"
LOCATION_BLOCK = """\
    location /api/provision {
        limit_req zone=spawnwp_provision burst=3 nodelay;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:9393;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
"""


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def add_provision_location() -> None:
    if not NGINX_CONF.is_file():
        return
    original = NGINX_CONF.read_text()
    if "zone=spawnwp_provision" in original:
        return
    if not PROXY_SNIPPET.is_file():
        source = Path(__file__).resolve().parent.parent / "spawnwp-proxy.conf"
        if not source.is_file():
            raise SystemExit("spawnwp-proxy.conf not found in release payload")
        PROXY_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
        PROXY_SNIPPET.write_text(source.read_text())
        os.chmod(PROXY_SNIPPET, 0o644)
    updated = re.sub(
        r"^(limit_req_zone \$binary_remote_addr zone=spawnwp_ingest:.*\n)",
        r"\1" + ZONE_LINE,
        original,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == original:
        raise SystemExit("spawnwp_ingest limit_req_zone anchor not found in nginx config")
    anchored = re.sub(
        r"^(    location /api/ingest/ \{\n(?:.*\n)*?    \}\n)",
        r"\1" + LOCATION_BLOCK,
        updated,
        count=1,
        flags=re.MULTILINE,
    )
    if anchored == updated:
        raise SystemExit("/api/ingest/ location anchor not found in nginx config")
    NGINX_CONF.write_text(anchored)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        NGINX_CONF.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def main() -> int:
    add_provision_location()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
