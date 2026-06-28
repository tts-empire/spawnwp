#!/usr/bin/env python3
"""Install host units required by cockpit-initiated updates."""

import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = Path(os.environ.get("SPAWNWP_SYSTEMD_ROOT", "/etc/systemd/system"))


def main() -> int:
    SYSTEMD_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("wp-cockpit.service", "spawnwp-update.service"):
        shutil.copy2(SOURCE / name, SYSTEMD_ROOT / name)
        os.chmod(SYSTEMD_ROOT / name, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "wp-cockpit"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
