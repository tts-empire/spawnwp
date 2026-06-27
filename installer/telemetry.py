#!/usr/bin/env python3
"""Minimal opt-in SpawnWP telemetry sender. Never blocks product operation."""
import json
import os
import platform
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/var/lib/spawnwp/telemetry")
CONSENT = ROOT / "consent.json"
PENDING = ROOT / "pending.json"
VERSION_FILE = Path(os.environ.get("SPAWNWP_VERSION_FILE", "/var/lib/spawnwp/VERSION"))
FEATURES_FILE = Path(os.environ.get("SPAWNWP_FEATURES_FILE", "/var/lib/spawnwp/features.json"))
ENVIRONMENTS_ROOT = Path(os.environ.get("SPAWNWP_ENVIRONMENTS_ROOT", "/srv"))
ENDPOINT = os.environ.get("SPAWNWP_TELEMETRY_ENDPOINT", "https://spawnwp.com/api/v1/telemetry")

def load(path):
    try: return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError): return {}

def disable():
    for path in (CONSENT, PENDING, ROOT / "installation-id"):
        path.unlink(missing_ok=True)

def payload(event="heartbeat"):
    consent = load(CONSENT)
    now = int(time.time())
    if not consent.get("enabled") or consent.get("expires_at", 0) <= now:
        disable(); return None
    identifier = (ROOT / "installation-id").read_text().strip()
    version = VERSION_FILE.read_text().strip()
    config = load(FEATURES_FILE)
    environments = sum(1 for path in ENVIRONMENTS_ROOT.iterdir() if (path / "compose.yaml").is_file())
    return {"installation_id": identifier, "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(), "spawnwp_version": version,
            "os_family": platform.system(), "os_version": platform.release(),
            "architecture": platform.machine(), "features": config,
            "counters": {"environments_current": environments}}

def send(event="heartbeat"):
    data = payload(event)
    if not data: return 0
    ROOT.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(data, indent=2) + "\n")
    request = urllib.request.Request(ENDPOINT, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if 200 <= response.status < 300: PENDING.unlink(missing_ok=True)
    except Exception:
        pass
    return 0

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "send"
    if command == "disable": disable()
    elif command == "payload": print(json.dumps(payload(), indent=2))
    else: raise SystemExit(send(sys.argv[2] if len(sys.argv) > 2 else "heartbeat"))
