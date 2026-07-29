#!/usr/bin/env python3
"""Fix two per-site gaps found via GitHub issue #13 (a mail-sending plugin causing
502s): WP-Cron was force-disabled on every spawned site with no substitute trigger,
and outgoing mail was only ever routed to Mailpit on the Development blueprint.

mail-capture.php is a plain file copy: WordPress rescans wp-content/mu-plugins/ on
every request, so a currently-running site picks it up immediately, no restart
needed (confirmed empirically, not assumed).

DISABLE_WP_CRON is a harder problem. wp-config.php does
`eval(getenv_docker('WORDPRESS_CONFIG_EXTRA', ''))` on every single request — the
value is never written into wp-config.php's own text, so `wp config delete` can
never find it there (confirmed: it always errors "is not defined in the file" on a
real site). The true value is whatever WORDPRESS_CONFIG_EXTRA was in the php
container's environment at the time it was last created — fixed for the container's
lifetime regardless of what compose.yaml says now.

And `spawnwp update` does NOT rewrite this site's own compose.yaml at all unless
it's /srv/wp-dev: the updater's "runtime" sync target is that one fixed path (see
TARGETS in updater/spawnwp), so every other already-spawned site's compose.yaml is
whatever new-project.sh copied at spawn time, frozen forever unless something edits
it explicitly. That line has been byte-for-byte identical
(`        define('DISABLE_WP_CRON', true);`) since SpawnWP's very first release
(verified via `git log -S`), so every existing site can be fixed with one exact,
literal removal — first in the site's own compose.yaml, then by recreating its php
container so the new environment actually takes effect.

Never start a site the user has deliberately stopped (cockpit's "down" action tears
containers down entirely, it doesn't pause them): only recreate php if it is
currently running. A stopped site's compose.yaml is fixed on disk either way, so it
gets the correct environment the next time it's started manually — no need to force
it now.

Idempotent throughout: the compose.yaml edit is a no-op once the line is gone, `wp
config delete` on an absent constant is a no-op, copying mail-capture.php over
itself is harmless, and `docker compose up -d` only recreates a container whose
resolved config actually changed. Non-fatal per site: anything that fails for one
site is logged and does not stop the migration from continuing to the next one.
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


DISABLE_WP_CRON_LINE = "        define('DISABLE_WP_CRON', true);\n"


def fix_site(site: Path) -> None:
    # mail-capture.php: a plain file copy, no running container needed.
    dest = site / "projects" / "primary" / "wp-content" / "mu-plugins" / "mail-capture.php"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_MAIL_CAPTURE, dest)
    os.chown(dest, 33, 33)
    os.chmod(dest, 0o644)

    # This site's own compose.yaml is frozen at whatever new-project.sh copied at
    # spawn time (spawnwp update never touches it unless this IS /srv/wp-dev) — the
    # line has been identical since SpawnWP's first release, so a literal removal
    # is safe everywhere.
    compose_path = site / "compose.yaml"
    compose_changed = False
    if compose_path.is_file():
        original = compose_path.read_text()
        updated = original.replace(DISABLE_WP_CRON_LINE, "")
        if updated != original:
            compose_path.write_text(updated)
            compose_changed = True

    # Also try wp-cli, in case DISABLE_WP_CRON was ever set some other way (e.g. by
    # hand via `wp config set`) — a no-op on every normal site, since the constant
    # was never textually in wp-config.php to begin with.
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "-u", "www-data", "php", "wp", "config", "delete", "DISABLE_WP_CRON"],
        cwd=site, capture_output=True, text=True,
    )
    if result.returncode != 0 and "is not defined" not in (result.stderr or ""):
        print(f"warning: {site.name}: could not clear DISABLE_WP_CRON via wp-cli: "
              f"{result.stderr.strip()}", file=sys.stderr)

    # The php container's environment is fixed at creation time: editing
    # compose.yaml on disk does nothing until the container is recreated. Only
    # recreate a site that is CURRENTLY running — never start one the user
    # deliberately stopped; it picks up the now-corrected compose.yaml the next
    # time it's started manually.
    if not compose_changed:
        return
    running = subprocess.run(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        cwd=site, capture_output=True, text=True,
    )
    if running.returncode == 0 and "php" in running.stdout.split():
        recreate = subprocess.run(
            ["docker", "compose", "up", "-d", "php"],
            cwd=site, capture_output=True, text=True,
        )
        if recreate.returncode != 0:
            print(f"warning: {site.name}: could not recreate the php container: "
                  f"{recreate.stderr.strip()}", file=sys.stderr)


def main() -> int:
    if not PROJECTS_ROOT.is_dir():
        return 0
    for site in sorted(PROJECTS_ROOT.iterdir()):
        if is_project(site):
            fix_site(site)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
