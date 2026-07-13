#!/usr/bin/env python3
"""Enable HTTP/2 on the TLS vhosts of existing installations.

The nginx template gained `http2` on its `listen 443 ssl` lines, but the RENDERED
vhost (/etc/nginx/sites-available/spawnwp) is only written by install.sh at
install time — so an update alone would ship the improvement to nobody. Requested
by @wpeasy (discussion #8): nginx here is already built --with-http_v2_module and
terminates TLS with ALPN, so h2 was one keyword away.

Idempotent. It must cope with two shapes of listen line, because both are out
there: the one our template renders (`listen 443 ssl;`) and the one Certbot
rewrites it into (`listen [::]:443 ssl ipv6only=on; # managed by Certbot`).
An early version only matched the first and would have given HTTP/2 to the
cockpit while silently leaving every actual site on HTTP/1.1.
"""

import os
import re
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))

# Any TLS listen line on 443 that does not already say http2 — trailing params
# (ipv6only=on) and Certbot's comment are preserved; nginx does not care where in
# the line the parameter sits.
LISTEN_RE = re.compile(
    r"^(\s*listen\s+(?:\[::\]:)?443\s+ssl)\b(?![^;\n]*\bhttp2\b)",
    re.MULTILINE,
)


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def enable_http2() -> None:
    if not NGINX_CONF.is_file():
        return
    original = NGINX_CONF.read_text()
    updated = LISTEN_RE.sub(r"\1 http2", original)
    if updated == original:
        return                                  # already enabled, or no TLS vhost yet
    NGINX_CONF.write_text(updated)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        NGINX_CONF.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def main() -> int:
    enable_http2()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
