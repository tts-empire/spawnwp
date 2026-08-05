#!/usr/bin/env python3
"""Repair duplicate per-site host ports created while another site was down.

Older new-project.sh releases considered only listening sockets. A stopped site's
ports therefore looked free even though its .env still owned them. This migration
keeps a running claimant where possible, remaps the other stopped services, and
updates their marked nginx blocks in the same transaction. It never starts a
service the operator deliberately stopped.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


PROJECTS_ROOT = Path(os.environ.get("SPAWNWP_PROJECTS_ROOT", "/srv"))
NGINX_CONF = Path(os.environ.get("SPAWNWP_NGINX_CONF", "/etc/nginx/sites-available/spawnwp"))
ALLOCATOR_PATH = Path(
    os.environ.get("SPAWNWP_PORT_ALLOCATOR", Path(__file__).resolve().parents[1] / "port_allocator.py")
)

spec = importlib.util.spec_from_file_location("spawnwp_port_allocator", ALLOCATOR_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load port allocator from {ALLOCATOR_PATH}")
allocator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(allocator)

PORT_SERVICES = {
    "WEB_PORT": "web",
    "MAILPIT_PORT": "mailpit",
    "ADMINER_PORT": "adminer",
}


class PortRepairError(RuntimeError):
    pass


def running_services(project: Path) -> set[str]:
    result = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PortRepairError(f"{project.name}: cannot inspect running services: {detail or 'docker compose failed'}")
    return set(result.stdout.split())


def build_repair_plan(
    projects_root: Path,
    active_ports: set[int],
    service_reader: Callable[[Path], set[str]] = running_services,
) -> dict[Path, dict[str, tuple[int, int]]]:
    claims = allocator.project_port_claims(projects_root)
    by_key: dict[str, dict[int, list[Path]]] = {
        key: {} for key in allocator.PORT_BASES
    }
    reserved = set(active_ports)
    for project, ports in claims:
        reserved.update(ports.values())
        for key, port in ports.items():
            by_key[key].setdefault(port, []).append(project)

    plan: dict[Path, dict[str, tuple[int, int]]] = {}
    service_cache: dict[Path, set[str]] = {}
    for key, start in allocator.PORT_BASES.items():
        service = PORT_SERVICES[key]
        for old_port, projects in sorted(by_key[key].items()):
            if len(projects) < 2:
                continue
            for project in projects:
                if project not in service_cache:
                    service_cache[project] = service_reader(project)
            running = [p for p in projects if service in service_cache[p]]
            if len(running) > 1:
                names = ", ".join(sorted(p.name for p in running))
                raise PortRepairError(
                    f"port {old_port} is claimed by multiple running {service} services: {names}"
                )
            keeper = running[0] if running else min(projects, key=lambda p: p.name)
            for project in sorted((p for p in projects if p != keeper), key=lambda p: p.name):
                new_port = allocator.next_available(start, reserved)
                plan.setdefault(project, {})[key] = (old_port, new_port)
    return plan


def replace_env_ports(original: str, updates: dict[str, tuple[int, int]], project: Path) -> str:
    seen: set[str] = set()
    lines = original.splitlines(keepends=True)
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r\n")
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key not in updates:
            continue
        old_port, new_port = updates[key]
        if value.strip() != str(old_port):
            raise PortRepairError(
                f"{project.name}: {key} changed while the repair was being planned"
            )
        newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""
        lines[index] = f"{key}={new_port}{newline}"
        seen.add(key)
    missing = set(updates) - seen
    if missing:
        raise PortRepairError(f"{project.name}: missing port values: {', '.join(sorted(missing))}")
    return "".join(lines)


def marked_block(conf: str, kind: str, project: str) -> tuple[int, int]:
    start_marker = f"# >>> SPAWNWP {kind} {project}"
    end_marker = f"# <<< SPAWNWP {kind} {project}"
    start = conf.find(start_marker)
    end = conf.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    if start < 0 or end < 0:
        raise PortRepairError(f"{project}: nginx {kind.lower()} markers not found")
    return start, end + len(end_marker)


def replace_in_marked_block(
    conf: str,
    kind: str,
    project: str,
    old_port: int,
    new_port: int,
) -> str:
    start, end = marked_block(conf, kind, project)
    block = conf[start:end]
    old_endpoint = f"127.0.0.1:{old_port}"
    if old_endpoint not in block:
        raise PortRepairError(
            f"{project}: nginx {kind.lower()} block does not reference port {old_port}"
        )
    updated = block.replace(old_endpoint, f"127.0.0.1:{new_port}")
    if kind == "SITE":
        updated = updated.replace(f"(port {old_port})", f"(port {new_port})")
    return conf[:start] + updated + conf[end:]


def rewrite_nginx(conf: str, plan: dict[Path, dict[str, tuple[int, int]]]) -> str:
    updated = conf
    for project, changes in sorted(plan.items(), key=lambda item: item[0].name):
        if "WEB_PORT" in changes:
            old_port, new_port = changes["WEB_PORT"]
            updated = replace_in_marked_block(updated, "SITE", project.name, old_port, new_port)
        for key in ("MAILPIT_PORT", "ADMINER_PORT"):
            if key in changes:
                old_port, new_port = changes[key]
                updated = replace_in_marked_block(updated, "ADMIN", project.name, old_port, new_port)
    return updated


def atomic_write(path: Path, content: str) -> None:
    current = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.spawnwp-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(current.st_mode))
        os.chown(temporary, current.st_uid, current.st_gid)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def nginx_command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, check=False)


def repair_duplicate_ports(
    projects_root: Path = PROJECTS_ROOT,
    nginx_conf: Path = NGINX_CONF,
    active_ports: set[int] | None = None,
    service_reader: Callable[[Path], set[str]] = running_services,
    command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = nginx_command,
) -> dict[Path, dict[str, tuple[int, int]]]:
    active = allocator.listening_ports() if active_ports is None else set(active_ports)
    plan = build_repair_plan(projects_root, active, service_reader)
    if not plan:
        return {}
    if not nginx_conf.is_file():
        raise PortRepairError(f"nginx configuration not found: {nginx_conf}")

    originals: dict[Path, str] = {
        project / ".env": (project / ".env").read_text(encoding="utf-8")
        for project in plan
    }
    original_nginx = nginx_conf.read_text(encoding="utf-8")
    updated_envs = {
        env_file: replace_env_ports(original, plan[env_file.parent], env_file.parent)
        for env_file, original in originals.items()
    }
    updated_nginx = rewrite_nginx(original_nginx, plan)

    try:
        for env_file, content in updated_envs.items():
            atomic_write(env_file, content)
        atomic_write(nginx_conf, updated_nginx)
        test = command_runner(["nginx", "-t"])
        if test.returncode != 0:
            raise PortRepairError((test.stderr or test.stdout or "nginx -t failed").strip())
        reload_result = command_runner(["systemctl", "reload", "nginx"])
        if reload_result.returncode != 0:
            raise PortRepairError(
                (reload_result.stderr or reload_result.stdout or "nginx reload failed").strip()
            )
    except Exception:
        for env_file, content in originals.items():
            atomic_write(env_file, content)
        atomic_write(nginx_conf, original_nginx)
        command_runner(["nginx", "-t"])
        command_runner(["systemctl", "reload", "nginx"])
        raise

    return plan


def main() -> int:
    try:
        plan = repair_duplicate_ports()
    except (OSError, PortRepairError, allocator.PortAllocationError) as exc:
        print(f"ERROR: duplicate port repair failed: {exc}", file=sys.stderr)
        return 1
    for project, changes in sorted(plan.items(), key=lambda item: item[0].name):
        detail = ", ".join(
            f"{key} {old}->{new}" for key, (old, new) in sorted(changes.items())
        )
        print(f"Repaired {project.name}: {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
