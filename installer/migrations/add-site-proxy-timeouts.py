#!/usr/bin/env python3
"""Add explicit proxy timeouts to the edge-nginx locations that front WordPress sites.

The edge vhost proxies every site through a `location` block that ends in
`proxy_intercept_errors on; error_page 502 503 504 =502 @wp_down;` (the "friendly"
502 page). None of those blocks ever set `proxy_read_timeout`/`proxy_send_timeout`,
so they fall back to nginx's compiled-in 60s default — well under PHP's own
`max_execution_time` (120s) and the inner container's `fastcgi_read_timeout` (300s).
Any synchronous request that runs past 60s gets cut off by this outer nginx and
shown to the visitor as "WordPress environment is not running", even though PHP
underneath is still alive and working (reported against a mail-sending plugin in
GitHub issue #13). Raising the outer timeout to 300s makes it match the inner one,
so the outer proxy is never the tightest constraint.

Idempotent: matches every location that ends in the `@wp_down` marker (the
rendered vhost only ever gets this template-written once, at install time — see
enable-http2.py's docstring for why an update alone never reaches it), and skips
any block that already has `proxy_read_timeout` before that marker.
"""

import os
import re
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))

BLOCK_RE = re.compile(
    r"(?P<pass>^[ \t]*proxy_pass\s+http://127\.0\.0\.1:\d+/?[^\n;]*;\n)"
    # No braces allowed in between: this must never cross the closing `}` of the
    # current location block into a sibling one (a real bug caught in testing —
    # an early version of this regex matched all the way from the cockpit's
    # unrelated /_spawnwp_auth location down to the first @wp_down it could find).
    r"(?P<between>(?:[^\n{}]*\n)*?)"
    r"(?P<marker>^[ \t]*proxy_intercept_errors\s+on;\n[ \t]*error_page\s+502\s+503\s+504\s+=502\s+@wp_down;)",
    re.MULTILINE,
)


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def add_timeout(match: re.Match) -> str:
    between = match.group("between")
    if "proxy_read_timeout" in between:
        return match.group(0)                   # already patched, leave as-is
    return (
        match.group("pass")
        + "        proxy_read_timeout 300s;\n"
        + "        proxy_send_timeout 300s;\n"
        + between
        + match.group("marker")
    )


def add_site_proxy_timeouts() -> None:
    if not NGINX_CONF.is_file():
        return
    original = NGINX_CONF.read_text()
    updated = BLOCK_RE.sub(add_timeout, original)
    if updated == original:
        return                                  # nothing to patch, or already done
    NGINX_CONF.write_text(updated)
    try:
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        NGINX_CONF.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def main() -> int:
    add_site_proxy_timeouts()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
