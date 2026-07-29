#!/usr/bin/env python3
"""Fix two per-site gaps found via GitHub issue #13 (a mail-sending plugin causing
502s): WP-Cron was force-disabled on every spawned site with no substitute trigger,
and outgoing mail was only ever routed to Mailpit on the Development blueprint.

`runtime/compose.yaml` no longer bakes `define('DISABLE_WP_CRON', true)` in for new
sites, and `apply-blueprint.sh` now installs `mail-capture.php` unconditionally. But
the official WordPress image only writes wp-config.php once, at first container
start — so already-spawned sites keep whatever was baked in back then regardless of
what the template says now. Both need an explicit per-site fix.

Idempotent both ways: `wp config delete` on a constant that isn't defined is a
no-op (WP-CLI exits 0), and copying mail-capture.php over itself is harmless.
Non-fatal per site: a site whose containers aren't running gets skipped and logged,
not treated as a fatal error for the whole migration.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECTS_ROOT = Path(os.environ.get("SPAWNWP_PROJECTS_ROOT", "/srv"))


def _default_mail_capture_source() -> Path:
    # This migration is packaged under payload/lib/installer/migrations/, but
    # runtime/ files (this one included) are packaged separately, straight under
    # payload/runtime/ — NOT under payload/lib/. So the source lives at a sibling
    # of this script's own "lib" root, not underneath it. Walking up for the
    # "payload" directory itself (rather than hardcoding a parents[N] index) keeps
    # this correct even if the packaging layout gains or loses a level later.
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if ancestor.name == "payload":
            return ancestor / "runtime" / "scripts" / "mail-capture.php"
    raise RuntimeError(f"could not find a 'payload' ancestor above {here}")


SOURCE_MAIL_CAPTURE = Path(os.environ["SPAWNWP_MAIL_CAPTURE_SOURCE"]) if "SPAWNWP_MAIL_CAPTURE_SOURCE" in os.environ \
    else _default_mail_capture_source()


def is_project(p: Path) -> bool:
    return p.is_dir() and (p / "compose.yaml").exists() and (p / "Makefile").exists()


def fix_site(site: Path) -> None:
    # mail-capture.php: a plain file copy, no running container needed.
    dest = site / "projects" / "primary" / "wp-content" / "mu-plugins" / "mail-capture.php"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_MAIL_CAPTURE, dest)
    os.chown(dest, 33, 33)
    os.chmod(dest, 0o644)

    # DISABLE_WP_CRON: needs a live container, since it edits the already-written
    # wp-config.php through WP-CLI rather than regenerating it.
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php", "wp", "config", "delete", "DISABLE_WP_CRON"],
        cwd=site, capture_output=True, text=True,
    )
    # "is not defined" means the constant is already gone (a fresh site, or a
    # second run of this migration) — not an error. Anything else (most likely
    # the container simply isn't running) is worth flagging, but shouldn't
    # abort the migration for every other site.
    if result.returncode != 0 and "is not defined" not in (result.stderr or ""):
        print(f"warning: {site.name}: could not clear DISABLE_WP_CRON (container down?): "
              f"{result.stderr.strip()}", file=sys.stderr)


def main() -> int:
    if not PROJECTS_ROOT.is_dir():
        return 0
    for site in sorted(PROJECTS_ROOT.iterdir()):
        if is_project(site):
            fix_site(site)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
