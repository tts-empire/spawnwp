#!/usr/bin/env python3
"""Refresh the docker-prune host unit (build-cache filter 168h -> 72h)."""

import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = Path(os.environ.get("SPAWNWP_SYSTEMD_ROOT", "/etc/systemd/system"))


def main() -> int:
    SYSTEMD_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "docker-prune.service", SYSTEMD_ROOT / "docker-prune.service")
    os.chmod(SYSTEMD_ROOT / "docker-prune.service", 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
