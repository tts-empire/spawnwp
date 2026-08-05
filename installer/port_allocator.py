#!/usr/bin/env python3
"""Allocate per-site loopback ports without reusing stopped projects' claims."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PORT_BASES = {
    "WEB_PORT": 8081,
    "MAILPIT_PORT": 8026,
    "ADMINER_PORT": 9002,
}


class PortAllocationError(RuntimeError):
    pass


def read_env_ports(env_file: Path) -> dict[str, int]:
    """Return the last value for every managed port in one Compose env file."""
    values: dict[str, str] = {}
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PortAllocationError(f"cannot read {env_file}: {exc}") from exc
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key in PORT_BASES:
            values[key] = value.strip()

    ports: dict[str, int] = {}
    for key, raw in values.items():
        try:
            port = int(raw)
        except ValueError as exc:
            raise PortAllocationError(f"{env_file}: {key} must be a numeric TCP port") from exc
        if not 1 <= port <= 65535:
            raise PortAllocationError(f"{env_file}: {key} must be between 1 and 65535")
        ports[key] = port
    return ports


def project_port_claims(projects_root: Path) -> list[tuple[Path, dict[str, int]]]:
    """Read every direct child's .env; a stopped project still owns its ports."""
    if not projects_root.is_dir():
        return []
    claims = []
    for project in sorted(projects_root.iterdir()):
        env_file = project / ".env"
        if project.is_dir() and env_file.is_file():
            claims.append((project, read_env_ports(env_file)))
    return claims


def listening_ports() -> set[int]:
    """Return host TCP listeners reported by iproute2's ss command."""
    result = subprocess.run(
        ["ss", "-H", "-ltn"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PortAllocationError(f"could not inspect listening TCP ports: {detail or 'ss failed'}")
    ports: set[int] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        raw = fields[3].rsplit(":", 1)[-1]
        if raw.isdigit():
            ports.add(int(raw))
    return ports


def next_available(start: int, reserved: set[int]) -> int:
    port = start
    while port in reserved and port <= 65535:
        port += 1
    if port > 65535:
        raise PortAllocationError(f"no available TCP port at or above {start}")
    reserved.add(port)
    return port


def allocate_ports(projects_root: Path, active_ports: set[int] | None = None) -> dict[str, int]:
    reserved = set(listening_ports() if active_ports is None else active_ports)
    for _, ports in project_port_claims(projects_root):
        reserved.update(ports.values())
    return {
        key: next_available(start, reserved)
        for key, start in PORT_BASES.items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    allocate = subparsers.add_parser("allocate")
    allocate.add_argument("--projects-root", type=Path, default=Path("/srv"))
    allocate.add_argument("--shell", action="store_true", help="print shell assignments")
    args = parser.parse_args(argv)
    try:
        ports = allocate_ports(args.projects_root)
    except PortAllocationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.shell:
        for key in PORT_BASES:
            print(f"{key}={ports[key]}")
    else:
        for key in PORT_BASES:
            print(f"{key}\t{ports[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
