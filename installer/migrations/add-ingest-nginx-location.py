#!/usr/bin/env python3
"""Add the /api/ingest/ nginx rate-limit zone and location to existing installations."""

import os
import re
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))
PROXY_SNIPPET = Path(os.environ.get("SPAWNWP_NGINX_SNIPPET", "/etc/nginx/snippets/spawnwp-proxy.conf"))

ZONE_LINE = "limit_req_zone $binary_remote_addr zone=spawnwp_ingest:10m rate=120r/m;\n"
LOCATION_BLOCK = """\
    location /api/ingest/ {
        limit_req zone=spawnwp_ingest burst=40 nodelay;
        include /etc/nginx/snippets/spawnwp-proxy.conf;
        proxy_pass http://127.0.0.1:9393;
        proxy_read_timeout 600s;
        proxy_request_buffering off;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
"""


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def install_proxy_snippet() -> None:
    if PROXY_SNIPPET.is_file():
        return
    source = Path(__file__).resolve().parent.parent / "spawnwp-proxy.conf"
    if not source.is_file():
        raise SystemExit("spawnwp-proxy.conf not found in release payload")
    PROXY_SNIPPET.parent.mkdir(parents=True, exist_ok=True)
    PROXY_SNIPPET.write_text(source.read_text())
    os.chmod(PROXY_SNIPPET, 0o644)


def add_ingest_location() -> None:
    if not NGINX_CONF.is_file():
        return
    install_proxy_snippet()
    original = NGINX_CONF.read_text()
    if "zone=spawnwp_ingest" in original:
        return
    updated = re.sub(
        r"^(limit_req_zone \$binary_remote_addr zone=spawnwp_auth:.*\n)",
        r"\1" + ZONE_LINE,
        original,
        count=1,
        flags=re.MULTILINE,
    )
    if updated == original:
        raise SystemExit("spawnwp_auth limit_req_zone anchor not found in nginx config")
    anchored = re.sub(
        r"^(    location /assets/ \{\n(?:.*\n)*?    \}\n)",
        r"\1" + LOCATION_BLOCK,
        updated,
        count=1,
        flags=re.MULTILINE,
    )
    if anchored == updated:
        raise SystemExit("/assets/ location anchor not found in nginx config")
    NGINX_CONF.write_text(anchored)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        NGINX_CONF.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def main() -> int:
    add_ingest_location()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
