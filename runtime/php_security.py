"""PHP runtime security status for the php2shell advisories."""

from __future__ import annotations

import re
import subprocess
import sys

PATCHED = {
    "8.2": (8, 2, 35),
    "8.3": (8, 3, 36),
    "8.4": (8, 4, 27),
    "8.5": (8, 5, 12),
}
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
LABEL = "org.spawnwp.php-version"
POLICY_LABEL = "org.spawnwp.php-security-policy"
SERIES_LABEL = "org.spawnwp.php-series"
POLICY = "php2shell-2026-10-21"


def status(version: str | None) -> dict[str, str]:
    """Return a conservative status; unknown vendor builds are never called fixed."""
    if not version:
        return {"status": "unverified", "php_full_version": "unknown", "minimum_fixed": "unknown"}
    match = VERSION_RE.fullmatch(version.strip())
    if not match:
        return {"status": "unverified", "php_full_version": version, "minimum_fixed": "unknown"}
    parsed = tuple(map(int, match.groups()))
    series = f"{parsed[0]}.{parsed[1]}"
    minimum = PATCHED.get(series)
    if minimum is None:
        return {"status": "vulnerable", "php_full_version": version, "minimum_fixed": "no verified upstream fix"}
    return {
        "status": "patched" if parsed >= minimum else "vulnerable",
        "php_full_version": version,
        "minimum_fixed": ".".join(map(str, minimum)),
    }


def image_report(reference: str) -> dict[str, str]:
    """Read image identity and inspect the PHP binary in a sealed one-off container."""
    result = subprocess.run(
        ["docker", "image", "inspect", "-f", f"{{{{.Id}}}}|{{{{index .Config.Labels \"{LABEL}\"}}}}|{{{{index .Config.Labels \"{SERIES_LABEL}\"}}}}|{{{{index .Config.Labels \"{POLICY_LABEL}\"}}}}", reference],
        capture_output=True, text=True,
    )
    if result.returncode:
        return {"image_id": "", **status(None)}
    parts = result.stdout.strip().split("|", 3)
    image_id = parts[0]
    version = parts[1] if len(parts) > 1 else ""
    series = parts[2] if len(parts) > 2 else ""
    policy = parts[3] if len(parts) > 3 else ""
    report = status(version or None)
    if series == "7.4":
        report = {"status": "vulnerable", "php_full_version": "unsupported legacy runtime", "minimum_fixed": "no verified image backport"}
    elif policy == POLICY and series in PATCHED:
        try:
            probe = subprocess.run(
                ["docker", "run", "--rm", "--network", "none", "--read-only",
                 "--memory", "128m", "--cpus", "0.5", "--pids-limit", "16",
                 "--user", "65534:65534", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges",
                 "--entrypoint", "php", reference, "-r", "echo PHP_VERSION;"],
                capture_output=True, text=True, timeout=20,
            )
            report = status(probe.stdout.strip() if probe.returncode == 0 else None)
        except (OSError, subprocess.TimeoutExpired):
            report = status(None)
        if report["status"] == "patched" and report.get("php_full_version") != "unknown":
            report["minimum_fixed"] = ".".join(map(str, PATCHED[series]))
    return {"image_id": image_id, "php_series": series or "unknown", **report}


def accepts_image(reference: str, acknowledged: bool, image_id: str = "") -> bool:
    """Scope risk acceptance to the exact cached image when one is selected."""
    report = image_report(reference)
    if report["status"] == "patched":
        return True
    return bool(acknowledged and image_id and report["image_id"] and image_id == report["image_id"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check-version":
        result = status(sys.argv[2])
        print(f"{result['status']}: PHP {result['php_full_version']} (minimum fixed: {result['minimum_fixed']})")
        raise SystemExit(0 if result["status"] == "patched" else 2)
    print("Usage: php_security.py --check-version <full-php-version>", file=sys.stderr)
    raise SystemExit(2)
