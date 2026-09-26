#!/usr/bin/env python3
"""Remove the temporary MCP licensing fixture from test installations."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


MODULE_ID = "spawnwp-mcp"
MODULES_ROOT = Path(os.environ.get("SPAWNWP_MODULES_ROOT", "/opt/spawnwp/modules"))
STATE_ROOT = Path(os.environ.get("SPAWNWP_MODULE_STATE_ROOT", "/var/lib/spawnwp/modules"))
SPAWNWP_CLI = Path(os.environ.get("SPAWNWP_CLI", "/usr/local/bin/spawnwp"))
FIXTURE_NAME = "SpawnWP MCP licensing fixture"
FIXTURE_DESCRIPTION = "Harmless premium licensing end-to-end test fixture."


def fixture_manifest() -> Path | None:
    path = MODULES_ROOT / MODULE_ID / "current" / "module.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if (value.get("id") == MODULE_ID
            and value.get("name") == FIXTURE_NAME
            and value.get("description") == FIXTURE_DESCRIPTION):
        return path
    return None


def main() -> int:
    if fixture_manifest() is None:
        return 0
    if not SPAWNWP_CLI.is_file():
        raise SystemExit("SpawnWP CLI is unavailable; cannot remove the MCP test fixture")
    result = subprocess.run(
        [str(SPAWNWP_CLI), "module", "remove", MODULE_ID, "--force", "--purge"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout or "MCP test fixture removal failed").strip())
    shutil.rmtree(MODULES_ROOT / MODULE_ID, ignore_errors=True)
    shutil.rmtree(STATE_ROOT / MODULE_ID, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
