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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from php_security import accepts_image
from runtime_images import prepare, preserve, selected, write_atomic as save_atomic

PREFIX = "::spawnwp-event::"
ALLOWED = {"7.4", "8.2", "8.3", "8.4", "8.5"}


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


def switch(project: Path, version: str, lock_root: Path = Path("/run/lock"),
           accept_vulnerable: bool = False, accepted_image_id: str = "") -> int:
    project = project.resolve()
    if version not in ALLOWED:
        emit("error", message="Unsupported PHP version")
        return 2
    if (project / ".spawnwp" / "runtime-migration-required.json").exists():
        emit("error", message="Customized runtime files require migration review before switching")
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
        running_services = subprocess.run(
            ["docker", "compose", "ps", "--services", "--filter", "status=running"],
            cwd=project, capture_output=True, text=True,
        )
        php_was_running = (
            running_services.returncode == 0 and "php" in running_services.stdout.split()
        )
        # The site keeps its WordPress version across a PHP switch, but the image
        # tag encodes it, so the target tag has to be derived from both.
        wp_version = env_value(original, "WP_VERSION", "latest") or "latest"
        image, wp_suffix, context_hash = image_identity(project, version, wp_version)
        os.environ["SPAWNWP_CONTEXT_HASH"] = context_hash
        if version == "7.4" and not accepts_image(image, accept_vulnerable, accepted_image_id):
            emit("error", message="PHP 7.4 is a vulnerable legacy runtime. Review the English LAB warning and explicitly accept the exact image before continuing.")
            return 2
        previous_env = dict(line.split("=",1) for line in original.splitlines()
                            if "=" in line and not line.startswith("#"))
        # Freeze the previous image before preparing can move a floating alias,
        # including a same-version refresh of a Down site.
        try:
            old_reference = selected(previous_env)
            if php_was_running:
                cid = subprocess.check_output(["docker","compose","ps","-q","php"],cwd=project,text=True).strip()
                old_reference = subprocess.check_output(["docker","inspect","-f","{{.Image}}",cid],text=True).strip()
            previous_ref = preserve(old_reference, previous, wp_version)
        except Exception as exc:
            emit("error", message="Previous image could not be retained: " + str(exc), previous=previous)
            return 1
        emit("start", previous=previous, target=version)
        # Build and verify before touching the site's environment or live containers.
        try:
            target = prepare(project, version, wp_version,
                             log=lambda line: emit("log", line=line))
            if version == "7.4" and not accepts_image(target, accept_vulnerable, accepted_image_id):
                raise RuntimeError("The prepared legacy image changed; review and accept its exact identity")
        except Exception as exc:
            emit("error", message=str(exc), previous=previous)
            return 1
        updated = replace_env(original, "PHP_VERSION", version)
        updated = replace_env(updated, "WP_IMAGE_SUFFIX", wp_suffix)
        updated = replace_env(updated, "PHP_IMAGE_REF", target)
        rollback = original
        started = False
        try:
            rollback = replace_env(original, "PHP_IMAGE_REF", previous_ref)
            record = project / ".spawnwp" / "runtime-rollback.json"
            entry = {"previous_ref":previous_ref, "environment":rollback,
                     "created_at":time.time(), "retain_until":time.time()+14*86400}
            save_atomic(record, json.dumps(entry)+"\n")
            history_path = project / ".spawnwp" / "runtime-history.json"
            history = json.loads(history_path.read_text()) if history_path.exists() else []
            history = [item for item in history if item.get("retain_until",0)>time.time()]
            history.append({key:value for key,value in entry.items() if key!="environment"})
            save_atomic(history_path,json.dumps(history)+"\n")
            if not (project / ".spawnwp" / "runtime-migrated.json").exists():
                emit("progress", phase="backup", percent=84, message="Saving the complete pre-migration backup", indeterminate=True)
                if run_simple(["python3","scripts/backup-runtime.py"], project):
                    raise RuntimeError("Pre-migration backup failed; runtime not changed")
            write_atomic(env_path, updated)
            if not php_was_running:
                metric_incr("php_switches")
                emit("complete", phase="complete", percent=100,
                     message=f"PHP {version} is selected; the site remains Down")
                return 0
            emit("progress", phase="start", percent=88, message="Starting the verified PHP image", indeterminate=False)
            started = True
            if run_simple(["docker","compose","up","-d","--no-deps","--no-build","--force-recreate","php"], project):
                raise RuntimeError("The PHP service could not be started")
            if not healthy(project):
                raise RuntimeError("PHP did not become healthy in time")
            if not site_ready(project):
                raise RuntimeError("WordPress did not pass HTTP/database readiness checks")
            save_atomic(project / ".spawnwp" / "runtime-migrated.json", json.dumps({"image_ref":target,"applied_at":time.time()})+"\n")
            metric_incr("php_switches")
            emit("complete", phase="complete", percent=100, message=f"PHP {version} is active")
            return 0
        except Exception as exc:
            write_atomic(env_path, rollback if started else original)
            if started and php_was_running:
                restored = run_simple(["docker","compose","up","-d","--no-deps","--no-build","--force-recreate","php"], project)
                if restored or not healthy(project) or not site_ready(project):
                    emit("error", message="Rollback could not restore site readiness; previous image and backup retained", previous=previous)
                    return 1
            emit("error", message=str(exc), previous=previous)
            return 1


def site_ready(project: Path) -> bool:
    # nginx resolves the php service name when it loads its configuration.
    # Replacing FPM can change its IP, so a graceful reload is required.
    services = subprocess.run(["docker","compose","ps","--services","--filter","status=running"],
                              cwd=project,capture_output=True,text=True)
    if "nginx" in services.stdout.split():
        for args in (["docker","compose","exec","-T","nginx","nginx","-t"],
                     ["docker","compose","exec","-T","nginx","nginx","-s","reload"]):
            if run_simple(args,project):
                return False
        check = ["docker","compose","exec","-T","php","php","-r",
                 '$c=stream_context_create(["http"=>["timeout"=>15,"follow_location"=>0,"ignore_errors"=>true]]);'
                 '$r=file_get_contents("http://nginx/",false,$c);'
                 'exit($r!==false && preg_match("~^HTTP/[^ ]+ [23][0-9]{2}~",$http_response_header[0]??"")?0:1);']
        for attempt in range(15):
            if run_simple(check,project)==0:
                break
            time.sleep(2)
        else:
            return False
    return run_simple(["docker","compose","exec","-T","-u","www-data","php",
                       "wp","db","query","SELECT 1","--skip-column-names"],project)==0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--accept-vulnerable", action="store_true")
    parser.add_argument("--accepted-image-id", default="")
    args = parser.parse_args()
    return switch(args.project, args.version, accept_vulnerable=args.accept_vulnerable,
                  accepted_image_id=args.accepted_image_id)


if __name__ == "__main__":
    raise SystemExit(main())
