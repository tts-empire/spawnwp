#!/usr/bin/env python3
"""Remove legacy HTTP Basic Auth."""

import os
import re
import shutil
import subprocess
from pathlib import Path

NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))
NGINX_ENABLED = Path(os.environ.get("SPAWNWP_NGINX_ENABLED", "/etc/nginx/sites-enabled"))
CONFIG_ENV = Path(os.environ.get("SPAWNWP_CONFIG", "/etc/spawnwp/config.env"))
REPORT = Path(os.environ.get("SPAWNWP_REPORT", "/root/spawnwp-credentials.txt"))

AUTH_LOCATION = """    location ~ ^/api/auth/(setup/(start|finish)|passkey/(start|finish)|fallback)$ {
        limit_req zone=spawnwp_auth burst=10 nodelay;
        proxy_pass http://127.0.0.1:9393;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        add_header Cache-Control "no-store" always;
    }
"""

AUTH_CHECK_LOCATION = """    location = /_spawnwp_auth {
        internal;
        proxy_pass http://127.0.0.1:9393/api/auth/check;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        proxy_set_header Cookie $http_cookie;
        proxy_set_header X-Real-IP $remote_addr;
    }
"""

LOGIN_LOCATION = "    location @spawnwp_login { return 303 /login; }\n"


def rewrite_nginx(content: str) -> str:
    content = re.sub(r"^\s*include /etc/nginx/cockpit-allowed\.conf;\s*\n", "", content, flags=re.MULTILINE)
    content = re.sub(
        r"^\s*auth_basic(?:_user_file)?\s+[^;]+;\s*\n",
        "", content, flags=re.MULTILINE,
    )
    content = re.sub(r"^.*#.*Basic Auth.*$\n?", "", content,
                     flags=re.MULTILINE | re.IGNORECASE)
    content = content.replace("error_page 401 =303 /login;", "error_page 401 = @spawnwp_login;")
    if "zone=spawnwp_auth:" not in content:
        content = "limit_req_zone $binary_remote_addr zone=spawnwp_auth:10m rate=30r/m;\n" + content
    marker_match = re.search(r"^\s*# __COCKPIT_PER_SITE__.*$", content, flags=re.MULTILINE)
    if not marker_match:
        raise RuntimeError("Unable to locate the cockpit insertion marker")
    server_start = content.rfind("\nserver {", 0, marker_match.start())
    cockpit_prefix = content[server_start:marker_match.start()]
    additions = ""
    if "location = /_spawnwp_auth" not in cockpit_prefix:
        additions += AUTH_CHECK_LOCATION
    if "location @spawnwp_login" not in cockpit_prefix:
        additions += LOGIN_LOCATION
    if "limit_req zone=spawnwp_auth" not in cockpit_prefix:
        additions += AUTH_LOCATION
    if additions:
        insert_at = marker_match.start()
        content = content[:insert_at] + additions + content[insert_at:]
    return content


def run(command: list[str], *, check: bool = True) -> None:
    subprocess.run(command, check=check, capture_output=True, text=True)


def nginx_targets() -> list[Path]:
    enabled = []
    if NGINX_ENABLED.is_dir():
        enabled = [path.resolve() for path in NGINX_ENABLED.iterdir() if path.is_file()]
    candidates = list(dict.fromkeys(enabled or [NGINX_CONF.resolve()]))
    return [path for path in candidates if path.is_file() and "__COCKPIT_PER_SITE__" in path.read_text()]


def migrate_nginx() -> None:
    targets = nginx_targets()
    if not targets:
        return
    originals = {path: path.read_text() for path in targets}
    try:
        for path, original in originals.items():
            updated = rewrite_nginx(original)
            if updated == original:
                continue
            backup = path.with_suffix(path.suffix + ".pre-0.3.0")
            if not backup.exists():
                shutil.copy2(path, backup)
            temporary = path.with_name(f".{path.name}.spawnwp-migration")
            temporary.write_text(updated)
            os.chmod(temporary, path.stat().st_mode & 0o777)
            os.replace(temporary, path)
        run(["nginx", "-t"])
        run(["systemctl", "reload", "nginx"])
    except Exception:
        for path, original in originals.items():
            path.write_text(original)
        run(["systemctl", "reload", "nginx"], check=False)
        raise


def remove_basic_auth_state() -> None:
    for path in (
        "/etc/nginx/.spawnwp-htpasswd", "/etc/nginx/.htpasswd",
    ):
        Path(path).unlink(missing_ok=True)


def clean_metadata() -> None:
    if CONFIG_ENV.is_file():
        lines = [line for line in CONFIG_ENV.read_text().splitlines()
                 if not line.startswith("BASIC_AUTH_")]
        CONFIG_ENV.write_text("\n".join(lines) + "\n")
    if REPORT.is_file():
        report = REPORT.read_text()
        report = re.sub(r"\nHTTP Basic Auth\n.*?(?=\nApplication setup code)", "\n", report, flags=re.DOTALL)
        REPORT.write_text(report.rstrip() + "\n")
        os.chmod(REPORT, 0o600)


def main() -> int:
    migrate_nginx()
    remove_basic_auth_state()
    run(["nginx", "-t"])
    clean_metadata()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
