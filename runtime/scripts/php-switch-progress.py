#!/usr/bin/env python3
"""Switch a project's PHP image while emitting structured progress events."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PREFIX = "::spawnwp-event::"
ALLOWED = {"7.4", "8.2", "8.3", "8.4"}


def emit(kind: str, **values) -> None:
    print(PREFIX + json.dumps({"type": kind, **values}, separators=(",", ":")), flush=True)


def metric_incr(key: str, n: int = 1) -> None:
    """Best-effort bump of a local aggregate counter (see lib-metrics.sh)."""
    path = os.environ.get("SPAWNWP_METRICS_FILE", "/var/lib/spawnwp/metrics.json")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(path) as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = {}
            except (OSError, ValueError):
                data = {}
            data[key] = int(data.get(key, 0)) + n
            with open(path + ".tmp", "w") as f:
                json.dump(data, f, sort_keys=True)
            os.replace(path + ".tmp", path)
    except Exception:
        pass


def env_value(text: str, key: str, default: str = "") -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return default


def replace_env(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    prefix = f"{key}="
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = prefix + value
            break
    else:
        lines.append(prefix + value)
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.php-switch")
    temporary.write_text(text)
    os.chmod(temporary, path.stat().st_mode & 0o777)
    os.replace(temporary, path)


def image_identity(project: Path, php_version: str, wp_version: str) -> tuple[str, str, str]:
    """Image tag, WP tag suffix and build-context hash, from the one definition
    in scripts/lib-image.sh — never recomputed here.

    Getting this from a second, hand-rolled copy is precisely what broke: this
    script used to build with SPAWNWP_CONTEXT_HASH unset, so compose stamped the
    image label "dev" and every subsequent deploy on that PHP version saw a hash
    mismatch and rebuilt from scratch (~5 minutes)."""
    def ask(*args: str) -> str:
        return subprocess.run(
            ["bash", "scripts/lib-image.sh", *args], cwd=project,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    return ask("tag", php_version, wp_version), ask("suffix", wp_version), ask("hash", wp_version)


def command(args: list[str], project: Path, *, structured: bool = False) -> int:
    process = subprocess.Popen(
        args, cwd=project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert process.stdout
    progress: dict[str, dict] = {}
    maximum = 10.0
    phase = "building"
    for raw in process.stdout:
        line = raw.rstrip()
        if not line:
            continue
        if not structured:
            emit("log", line=line)
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            emit("log", line=line)
            continue
        identifier = str(item.get("id") or item.get("vertex") or len(progress))
        status = str(item.get("status") or item.get("text") or item.get("name") or "Building")
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        current = item.get("current", detail.get("current"))
        total = item.get("total", detail.get("total"))
        completed = bool(item.get("completed")) or status.lower() in {"done", "complete", "completed", "cached"}
        progress[identifier] = {"current": current, "total": total, "completed": completed}
        lowered = status.lower()
        if any(word in lowered for word in ("pull", "download", "fetch", "resolve")):
            phase = "download"
        elif "export" in lowered or "unpack" in lowered:
            phase = "export"
        else:
            phase = "build"
        if isinstance(total, (int, float)) and total > 0:
            ratio = min(float(current or 0), float(total)) / float(total)
            start, span = (70, 12) if phase == "export" else (10, 50)
            candidate = start + ratio * span
            maximum = max(maximum, candidate)
            percent = min(round(maximum), 82)
        else:
            percent = None
        emit("progress", phase=phase, percent=percent, message=status, indeterminate=percent is None)
        emit("log", line=status)
    return process.wait()


def run_simple(args: list[str], project: Path) -> int:
    return command(args, project, structured=False)


def healthy(project: Path, timeout: int = 180) -> bool:
    identity = subprocess.run(
        ["docker", "compose", "ps", "-q", "php"], cwd=project,
        capture_output=True, text=True,
    ).stdout.strip()
    if not identity:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}", identity],
            capture_output=True, text=True,
        )
        state = result.stdout.strip()
        if state in {"healthy", "running"}:
            return True
        if state in {"unhealthy", "exited", "dead"}:
            return False
        time.sleep(2)
    return False


def switch(project: Path, version: str, lock_root: Path = Path("/run/lock")) -> int:
    project = project.resolve()
    if version not in ALLOWED:
        emit("error", message="Unsupported PHP version")
        return 2
    env_path = project / ".env"
    if not env_path.is_file() or not (project / "compose.yaml").is_file():
        emit("error", message="Invalid SpawnWP environment")
        return 2

    lock_path = lock_root / f"spawnwp-php-switch-{project.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit("error", message="A PHP switch is already running for this environment")
            return 1

        original = env_path.read_text()
        previous = env_value(original, "PHP_VERSION", "8.3")
        # The site keeps its WordPress version across a PHP switch, but the image
        # tag encodes it, so the target tag has to be derived from both.
        wp_version = env_value(original, "WP_VERSION", "latest") or "latest"
        image, wp_suffix, context_hash = image_identity(project, version, wp_version)
        cached = subprocess.run(
            ["docker", "image", "inspect", image], capture_output=True,
        ).returncode == 0
        emit("start", previous=previous, target=version, first_download=not cached)
        updated = replace_env(original, "PHP_VERSION", version)
        # Also (re)write WP_IMAGE_SUFFIX: sites created before 0.5.20 don't have
        # it, and compose needs it to resolve the same tag we just computed.
        updated = replace_env(updated, "WP_IMAGE_SUFFIX", wp_suffix)
        write_atomic(env_path, updated)
        # Without this the build stamps the label "dev" and poisons the cache for
        # every later deploy on this PHP version.
        os.environ["SPAWNWP_CONTEXT_HASH"] = context_hash

        started = False
        try:
            if cached:
                emit("progress", phase="cache", percent=82, message=f"PHP {version} image is already available", indeterminate=False)
            else:
                emit("progress", phase="download", percent=None, message=f"First use of PHP {version}: downloading and compiling the image", indeterminate=True)
                if command(["docker", "compose", "--progress", "json", "build", "php"], project, structured=True) != 0:
                    raise RuntimeError("The PHP image build failed")
            emit("progress", phase="start", percent=88, message="Restarting the PHP service", indeterminate=False)
            started = True
            if run_simple(["docker", "compose", "up", "-d", "php"], project) != 0:
                raise RuntimeError("The PHP service could not be started")
            emit("progress", phase="health", percent=95, message="Waiting for the PHP health check", indeterminate=True)
            if not healthy(project):
                raise RuntimeError("PHP did not become healthy in time")
            metric_incr("php_switches")
            emit("complete", phase="complete", percent=100, message=f"PHP {version} is active")
            return 0
        except Exception as exc:
            write_atomic(env_path, original)
            emit("log", line=f"Restored PHP_VERSION={previous}")
            if started:
                run_simple(["docker", "compose", "up", "-d", "php"], project)
            emit("error", message=str(exc), previous=previous)
            return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    return switch(args.project, args.version)


if __name__ == "__main__":
    raise SystemExit(main())
