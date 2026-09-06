#!/usr/bin/env python3
"""Make existing site copies compatible with state-aware RAM admission control.

Spawned sites keep a frozen compose.yaml, so updating only /srv/wp-dev would
leave their PHP container fixed at 512 MiB. This migration changes that one
limit to the internal .env variable and derives its value from each site's
current zz-site.ini memory_limit. It never recreates or starts a container;
Docker applies the new cap on the site's next explicit Up or PHP-settings edit.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

MIB = 1024 ** 2
PHP_MIN_MIB = 512
PHP_HEADROOM_MIB = 256
PROJECTS_ROOT = Path(os.environ.get("SPAWNWP_PROJECTS_ROOT", "/srv"))


def is_project(path: Path) -> bool:
    return path.is_dir() and (path / "compose.yaml").is_file() and (path / ".env").is_file()


def size_mib(value: str) -> int:
    match = re.fullmatch(r"([0-9]{1,4})([KMG])", value.strip(), re.I)
    if not match:
        return 256
    number, unit = int(match.group(1)), match.group(2).upper()
    if unit == "G":
        return number * 1024
    if unit == "K":
        return max(1, (number + 1023) // 1024)
    return number


def php_memory_limit(site: Path) -> str:
    ini = site / "docker" / "php" / "zz-site.ini"
    try:
        text = ini.read_text()
    except OSError:
        return "256M"
    match = re.search(r"^\s*memory_limit\s*=\s*([0-9]{1,4}[KMG])\s*$", text, re.M | re.I)
    return match.group(1).upper() if match else "256M"


def replace_env(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def update_compose(text: str) -> str:
    start = text.find("\n  php:\n")
    end = text.find("\n  db:\n", start + 1)
    if start < 0 or end < 0:
        return text
    section = text[start:end]
    section = re.sub(
        r"(^\s+memory:\s*)512M\s*$",
        r"\g<1>${SPAWNWP_PHP_CONTAINER_MEMORY:-512M}",
        section,
        count=1,
        flags=re.M,
    )
    return text[:start] + section + text[end:]


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.ram-admission")
    temporary.write_text(text)
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def upgrade_site(site: Path) -> bool:
    compose_path, env_path = site / "compose.yaml", site / ".env"
    original_compose = compose_path.read_text()
    original_env = env_path.read_text()
    container_mib = max(PHP_MIN_MIB, size_mib(php_memory_limit(site)) + PHP_HEADROOM_MIB)
    updated_compose = update_compose(original_compose)
    updated_env = replace_env(original_env, "SPAWNWP_PHP_CONTAINER_MEMORY", f"{container_mib}M")
    changed = False
    if updated_compose != original_compose:
        write_atomic(compose_path, updated_compose)
        changed = True
    if updated_env != original_env:
        write_atomic(env_path, updated_env)
        changed = True
    return changed


def main() -> int:
    if not PROJECTS_ROOT.is_dir():
        return 0
    for site in sorted(PROJECTS_ROOT.iterdir()):
        if not is_project(site):
            continue
        try:
            upgrade_site(site)
        except OSError as exc:
            print(f"warning: {site.name}: could not add RAM admission limits: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
