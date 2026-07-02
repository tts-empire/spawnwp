#!/usr/bin/env python3
"""Install and enable the image auto-delete units (System info feature)."""

import os
import shutil
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
SYSTEMD_ROOT = Path(os.environ.get("SPAWNWP_SYSTEMD_ROOT", "/etc/systemd/system"))


def main() -> int:
    SYSTEMD_ROOT.mkdir(parents=True, exist_ok=True)
    for name in ("spawnwp-image-gc.service", "spawnwp-image-gc.timer"):
        shutil.copy2(SOURCE / name, SYSTEMD_ROOT / name)
        os.chmod(SYSTEMD_ROOT / name, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "spawnwp-image-gc.timer"], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
