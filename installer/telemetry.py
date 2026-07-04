#!/usr/bin/env python3
"""Minimal opt-in SpawnWP telemetry sender. Never blocks product operation."""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/var/lib/spawnwp/telemetry")
CONSENT = ROOT / "consent.json"
PENDING = ROOT / "pending.json"
VERSION_FILE = Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION"))
FEATURES_FILE = Path(os.environ.get("SPAWNWP_FEATURES_FILE", "/var/lib/spawnwp/features.json"))
ENVIRONMENTS_ROOT = Path(os.environ.get("SPAWNWP_ENVIRONMENTS_ROOT", "/srv"))
METRICS_FILE = Path(os.environ.get("SPAWNWP_METRICS_FILE", "/var/lib/spawnwp/metrics.json"))
ENDPOINT = os.environ.get("SPAWNWP_TELEMETRY_ENDPOINT", "https://spawnwp.com/api/v1/telemetry")
CONSENT_SECONDS = 90 * 24 * 60 * 60
NOTICE_VERSION = "3"

# Aggregate machine counters shared only with notice-v3 consents. This whitelist
# must match the receiver's (ops/telemetry-receiver/app.py METRIC_KEYS).
METRIC_KEYS = (
    "creates_total", "creates_failed", "healthcheck_timeouts",
    "create_warm_count", "create_warm_seconds_sum", "create_warm_seconds_max",
    "create_cold_count", "create_cold_seconds_sum", "create_cold_seconds_max",
    "blueprint_clean", "blueprint_demo", "blueprint_development", "blueprint_custom", "blueprint_captures",
    "sites_temporary_created", "sites_expired_auto", "php_settings_customized",
    "destroys_total", "php_switches", "image_refreshes", "image_deletes",
    "wp_cli_commands",
)

def load(path):
    try: return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError): return {}

def set_feature(enabled):
    features = load(FEATURES_FILE)
    features["telemetry"] = bool(enabled)
    FEATURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEATURES_FILE.write_text(json.dumps(features, separators=(",", ":")) + "\n")

def post(data):
    request = urllib.request.Request(ENDPOINT, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return 200 <= response.status < 300
    except Exception:
        return False

def disable(notify=True):
    identifier_path = ROOT / "installation-id"
    if notify and identifier_path.is_file():
        post({"installation_id": identifier_path.read_text().strip(), "event": "disable",
              "timestamp": datetime.now(timezone.utc).isoformat()})
    for path in (CONSENT, PENDING, ROOT / "installation-id"):
        path.unlink(missing_ok=True)
    set_feature(False)

def enable():
    ROOT.mkdir(parents=True, exist_ok=True)
    identifier = str(uuid.uuid4())
    now = int(time.time())
    (ROOT / "installation-id").write_text(identifier + "\n")
    CONSENT.write_text(json.dumps({"enabled": True, "notice_version": NOTICE_VERSION,
                                   "consented_at": now, "expires_at": now + CONSENT_SECONDS}) + "\n")
    PENDING.unlink(missing_ok=True)
    set_feature(True)
    send("installation")

def payload(event="heartbeat"):
    consent = load(CONSENT)
    now = int(time.time())
    if not consent.get("enabled") or consent.get("expires_at", 0) <= now:
        disable(); return None
    identifier = (ROOT / "installation-id").read_text().strip()
    version = VERSION_FILE.read_text().strip()
    config = load(FEATURES_FILE)
    environments = sum(
        1 for path in ENVIRONMENTS_ROOT.iterdir()
        if (path / "compose.yaml").is_file()
        and not (path / ".spawnwp" / "template-only").is_file()
    )
    data = {"installation_id": identifier, "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(), "spawnwp_version": version,
            "os_family": platform.system(), "os_version": platform.release(),
            "architecture": platform.machine(), "features": config,
            "counters": {"environments_current": environments}}
    # Extended aggregates (notice v3 only): consents given under the v2 notice
    # keep the exact minimal payload they agreed to, until natural renewal.
    try:
        v3 = int(consent.get("notice_version", "0")) >= 3
    except ValueError:
        v3 = False
    if v3:
        data["metrics"] = collect_metrics()
        data["hardware"] = collect_hardware()
    return data

def collect_metrics():
    raw = load(METRICS_FILE)
    return {key: min(int(raw[key]), 10**9) for key in METRIC_KEYS
            if isinstance(raw.get(key), int) and raw[key] >= 0}

def docker_json(args):
    try:
        out = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=5)
        return out.stdout if out.returncode == 0 else ""
    except Exception:
        return ""

def parse_gb(size):
    import re
    match = re.match(r"([0-9.]+)\s*([kKMGT]?B)", size.strip())
    if not match:
        return 0.0
    units = {"B": 1e-9, "kB": 1e-6, "KB": 1e-6, "MB": 1e-3, "GB": 1.0, "TB": 1e3}
    return float(match.group(1)) * units.get(match.group(2), 0.0)

def collect_hardware():
    ram_gb = 0
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram_gb = round(int(line.split()[1]) / 1024 / 1024)
                break
    except OSError:
        pass
    disk = shutil.disk_usage("/")
    images_gb = cache_gb = 0
    for line in docker_json(["system", "df", "--format", "json"]).splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("Type") == "Images":
            images_gb = round(parse_gb(entry.get("Size", "0B")))
        elif entry.get("Type") == "Build Cache":
            cache_gb = round(parse_gb(entry.get("Size", "0B")))
    php_versions = len([t for t in docker_json(["image", "ls", "wp-dev-php",
                                                "--format", "{{.Tag}}"]).split() if t])
    return {"cpu_count": os.cpu_count() or 0, "ram_gb": ram_gb,
            "disk_total_gb": round(disk.total / 1e9), "disk_free_gb": round(disk.free / 1e9),
            "docker_images_gb": images_gb, "build_cache_gb": cache_gb,
            "php_versions": php_versions}

def send(event="heartbeat"):
    data = payload(event)
    if not data: return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(data, indent=2) + "\n")
    if post(data): PENDING.unlink(missing_ok=True)
    return 0

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "send"
    if command == "enable": enable()
    elif command == "disable": disable()
    elif command == "payload": print(json.dumps(payload(), indent=2))
    else: raise SystemExit(send(sys.argv[2] if len(sys.argv) > 2 else "heartbeat"))
