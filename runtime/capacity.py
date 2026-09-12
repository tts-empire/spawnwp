"""State-aware RAM admission control for SpawnWP container operations.

The host's live RAM usage is deliberately not the admission metric: Linux uses
otherwise-idle memory for cache and Docker may grow each container up to its
cgroup limit. We therefore budget the limits of running SpawnWP containers,
plus in-flight reservations, against physical RAM minus an operating-system
reserve. Stopped sites consume no budget.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
from pathlib import Path

MIB = 1024 ** 2
GIB = 1024 ** 3
CONFIG_ENV = Path(os.environ.get("SPAWNWP_CONFIG_ENV", "/etc/spawnwp/config.env"))
MIN_SYSTEM_RESERVE = 512 * MIB
MAX_SYSTEM_RESERVE = 1 * GIB
SYSTEM_RESERVE_RATIO = 0.20
PHP_MIN_CONTAINER_MEMORY = 512 * MIB
PHP_HEADROOM = 256 * MIB
ADMISSION_ENFORCE = "enforce"
ADMISSION_ADVISORY = "advisory"

_lock = threading.RLock()
_reservations: dict[str, int] = {}


class CapacityError(RuntimeError):
    """A capacity check could not be completed or the request does not fit."""


class CapacityExceeded(CapacityError):
    """The requested allocation is valid but does not fit the current budget."""


def admission_policy() -> str:
    """Read the host-wide RAM admission policy.

    The file is intentionally read for each operation so a root-only config
    change takes effect without restarting the Cockpit. Invalid values fail
    closed instead of silently weakening the safety guard.
    """
    raw = ""
    try:
        if CONFIG_ENV.is_file():
            for line in CONFIG_ENV.read_text().splitlines():
                if line.startswith("SPAWNWP_RAM_ADMISSION="):
                    raw = line.partition("=")[2].strip().lower()
                    break
    except OSError as exc:
        raise CapacityError("Unable to read SpawnWP configuration") from exc
    policy = raw or ADMISSION_ENFORCE
    if policy not in {ADMISSION_ENFORCE, ADMISSION_ADVISORY}:
        raise CapacityError(
            "Invalid SPAWNWP_RAM_ADMISSION configuration: use 'enforce' or 'advisory'"
        )
    return policy


def parse_memory_bytes(value: object) -> int:
    """Parse Docker Compose memory values (bytes or strings such as ``512M``)."""
    if isinstance(value, int):
        return max(value, 0)
    raw = str(value or "").strip()
    if raw.isdigit():
        return int(raw)
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt])(?:i?b)?", raw, re.I)
    if not match:
        raise CapacityError(f"Invalid container memory limit: {raw or 'missing'}")
    multiplier = {"k": 1024, "m": MIB, "g": GIB, "t": 1024 ** 4}[match.group(2).lower()]
    return int(float(match.group(1)) * multiplier)


def php_container_memory_bytes(memory_limit: str) -> int:
    """Return PHP's cgroup cap: at least 512 MiB, with 256 MiB headroom."""
    return max(PHP_MIN_CONTAINER_MEMORY, parse_memory_bytes(memory_limit) + PHP_HEADROOM)


def format_compose_memory(value: int) -> str:
    """Stable .env representation for a byte value derived in MiB."""
    return f"{(value + MIB - 1) // MIB}M"


def host_total_bytes(meminfo: Path = Path("/proc/meminfo")) -> int:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError) as exc:
        raise CapacityError("Unable to determine total host RAM") from exc
    raise CapacityError("Unable to determine total host RAM")


def system_reserve_bytes(total: int) -> int:
    return max(MIN_SYSTEM_RESERVE, min(MAX_SYSTEM_RESERVE, int(total * SYSTEM_RESERVE_RATIO)))


def _spawnwp_working_dir(labels: dict, projects_root: Path) -> Path | None:
    raw = str(labels.get("com.docker.compose.project.working_dir", ""))
    if not raw:
        return None
    try:
        working = Path(raw).resolve()
        relative = working.relative_to(projects_root.resolve())
    except (OSError, ValueError):
        return None
    return working if len(relative.parts) == 1 else None


def running_container_limits(projects_root: Path) -> list[dict]:
    """Return actual cgroup limits for all running SpawnWP containers."""
    try:
        listed = subprocess.run(
            ["docker", "ps", "-q", "--filter", "status=running"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CapacityError("Unable to inspect running Docker containers") from exc
    if listed.returncode != 0:
        raise CapacityError("Unable to inspect running Docker containers")
    ids = [item for item in listed.stdout.splitlines() if item]
    if not ids:
        return []
    try:
        inspected = subprocess.run(
            ["docker", "inspect", *ids], capture_output=True, text=True, timeout=20,
        )
        payload = json.loads(inspected.stdout) if inspected.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise CapacityError("Unable to inspect running Docker container limits") from exc
    if not isinstance(payload, list):
        raise CapacityError("Unable to inspect running Docker container limits")

    records = []
    for item in payload:
        if not isinstance(item, dict) or not item.get("State", {}).get("Running"):
            continue
        labels = item.get("Config", {}).get("Labels") or {}
        project = _spawnwp_working_dir(labels, projects_root)
        if project is None:
            continue
        limit = int(item.get("HostConfig", {}).get("Memory") or 0)
        if limit <= 0:
            raise CapacityError(
                f"Running SpawnWP service {labels.get('com.docker.compose.service', 'unknown')} "
                "has no finite container memory limit"
            )
        records.append({
            "id": str(item.get("Id", ""))[:12],
            "project": str(project),
            "service": str(labels.get("com.docker.compose.service", "")),
            "bytes": max(limit, 0),
        })
    return records


def configured_service_limits(project: Path, extra_env: dict | None = None) -> dict[str, int]:
    """Resolve active (non-profile) service limits without logging compose data."""
    env = {**os.environ, **(extra_env or {})}
    project_env: dict[str, str] = {}
    try:
        for line in (project / ".env").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                project_env[key.strip()] = value.strip()
    except OSError:
        pass
    raw_profiles = (extra_env or {}).get(
        "COMPOSE_PROFILES", env.get("COMPOSE_PROFILES", project_env.get("COMPOSE_PROFILES", ""))
    )
    active_profiles = {item.strip() for item in raw_profiles.split(",") if item.strip()}
    try:
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=project, env=env, capture_output=True, text=True, timeout=20,
        )
        payload = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise CapacityError(f"Unable to resolve container limits for {project.name}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("services"), dict):
        raise CapacityError(f"Unable to resolve container limits for {project.name}")

    limits: dict[str, int] = {}
    for name, service in payload["services"].items():
        if not isinstance(service, dict):
            continue
        profiles = set(service.get("profiles") or [])
        if profiles and not profiles.intersection(active_profiles):
            continue
        memory = (service.get("deploy", {}).get("resources", {}).get("limits", {})
                  .get("memory"))
        limit = parse_memory_bytes(memory)
        if limit <= 0:
            raise CapacityError(f"Service {name} has no finite container memory limit")
        limits[str(name)] = limit
    if not limits:
        raise CapacityError(f"No active services found for {project.name}")
    return limits


def _snapshot_unlocked(projects_root: Path, records: list[dict] | None = None) -> dict:
    policy = admission_policy()
    total = host_total_bytes()
    reserve = system_reserve_bytes(total)
    allocatable = max(total - reserve, 0)
    records = running_container_limits(projects_root) if records is None else records
    running = sum(int(item.get("bytes", 0)) for item in records)
    pending = sum(_reservations.values())
    committed = running + pending
    available = max(allocatable - committed, 0)
    return {
        "total_bytes": total,
        "system_reserve_bytes": reserve,
        "allocatable_bytes": allocatable,
        "running_committed_bytes": running,
        "pending_bytes": pending,
        "committed_bytes": committed,
        "available_bytes": available,
        "overcommitted": committed > allocatable,
        "admission_policy": policy,
        "running_containers": len(records),
        "records": records,
    }


def capacity_snapshot(projects_root: Path) -> dict:
    with _lock:
        return _snapshot_unlocked(projects_root)


def _human(value: int) -> str:
    if value >= GIB:
        return f"{value / GIB:.1f} GiB"
    return f"{(value + MIB - 1) // MIB} MiB"


def _rejection(snapshot: dict, requested: int) -> CapacityExceeded:
    return CapacityExceeded(
        "Insufficient RAM capacity: "
        f"host {_human(snapshot['total_bytes'])}; "
        f"system reserve {_human(snapshot['system_reserve_bytes'])}; "
        f"committed {_human(snapshot['committed_bytes'])}; "
        f"requested {_human(requested)}; "
        f"available {_human(snapshot['available_bytes'])}. "
        "Bring another site Down or lower a running site's PHP memory_limit."
    )


def _reserve_unlocked(snapshot: dict, requested: int) -> str | None:
    requested = max(int(requested), 0)
    if requested == 0:
        return None
    if (requested > snapshot["available_bytes"]
            and snapshot["admission_policy"] == ADMISSION_ENFORCE):
        raise _rejection(snapshot, requested)
    token = secrets.token_urlsafe(18)
    _reservations[token] = requested
    return token


def reserve_project_start(projects_root: Path, project: Path,
                          extra_env: dict | None = None) -> tuple[str | None, int]:
    """Atomically reserve only the containers missing from this project's stack."""
    with _lock:
        snapshot = _snapshot_unlocked(projects_root)
        desired = sum(configured_service_limits(project, extra_env).values())
        project_path = str(project.resolve())
        already_running = sum(
            item["bytes"] for item in snapshot["records"] if item["project"] == project_path
        )
        requested = max(desired - already_running, 0)
        return _reserve_unlocked(snapshot, requested), requested


def reserve_new_project(projects_root: Path, template: Path,
                        extra_env: dict | None = None) -> tuple[str | None, int]:
    """Atomically reserve a complete new stack resolved from the template."""
    with _lock:
        snapshot = _snapshot_unlocked(projects_root)
        requested = sum(configured_service_limits(template, extra_env).values())
        return _reserve_unlocked(snapshot, requested), requested


def reserve_php_resize(projects_root: Path, project: Path,
                       new_limit: int) -> tuple[str | None, int]:
    """Reserve the positive delta for a running PHP container; Down means zero."""
    with _lock:
        snapshot = _snapshot_unlocked(projects_root)
        project_path = str(project.resolve())
        current = sum(
            item["bytes"] for item in snapshot["records"]
            if item["project"] == project_path and item["service"] == "php"
        )
        requested = max(new_limit - current, 0) if current else 0
        return _reserve_unlocked(snapshot, requested), requested


def release_reservation(token: str | None) -> None:
    if not token:
        return
    with _lock:
        _reservations.pop(token, None)


def public_snapshot(projects_root: Path) -> dict:
    """Capacity payload in MiB for the Cockpit API; omit container identifiers."""
    snapshot = capacity_snapshot(projects_root)
    keys = (
        "total_bytes", "system_reserve_bytes", "allocatable_bytes",
        "running_committed_bytes", "pending_bytes", "committed_bytes", "available_bytes",
    )
    payload = {key.removesuffix("_bytes") + "_mb": snapshot[key] // MIB for key in keys}
    payload.update(
        overcommitted=snapshot["overcommitted"],
        admission_policy=snapshot["admission_policy"],
        running_containers=snapshot["running_containers"],
        policy="running-container-limits",
    )
    return payload
