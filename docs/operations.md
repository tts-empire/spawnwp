---
description: Operate and maintain a SpawnWP server, including services, logs, TLS renewal, timers and routine diagnostics.
---

# Operations

Day-to-day running of a SpawnWP host.

## Where things live

| Path | Contents |
|---|---|
| `/srv/wp-dev/` | Internal stack template used to create environments; not a site |
| `/srv/<name>/` | Each spawned site (Compose project, `.env`, backups, `wp-content`) |
| `/srv/wp-cockpit/` | The cockpit app |
| `/etc/nginx/sites-available/spawnwp` | Both vhosts (content + cockpit) |
| `/root/spawnwp-credentials.txt` | Your install secrets (`600`) |

## systemd services

```bash
systemctl status wp-cockpit            # the dashboard app
systemctl list-timers | grep docker-prune
```

| Unit | Role |
|---|---|
| `wp-cockpit.service` | Runs the cockpit (uvicorn on `127.0.0.1:9393`) |
| `docker-prune.timer` | Weekly Docker build-cache prune of layers unused for 72h (safe; never touches volumes). Site creation also trims stale cache right after each image build. |
| `spawnwp-image-gc.timer` | Daily check for the optional auto-delete of unused PHP images (System tab setting; disabled by default) |
| `spawnwp-site-expiry.timer` | Hourly destruction of expired temporary sites (sites created with a lifetime) |

## Updating SpawnWP

The cockpit checks the latest stable GitHub Release and shows an **Updates** indicator.
Checks are anonymous once the repository is public and remain independent from telemetry.
Review the release notes and use **Install update** on the Updates page. The signed
update runs as a separate systemd job so it continues while the cockpit restarts.
The root command remains available as a recovery path:

```bash
spawnwp update --check
sudo spawnwp update
```

Install a specific stable release or return to the previous installed release:

```bash
sudo spawnwp update --version 0.2.0
sudo spawnwp rollback
```

Every release manifest is signed with SpawnWP's Ed25519 release key. The updater verifies
the signature, archive checksum and every managed file before activation. It backs up the
current control-plane files, validates Python and nginx, restarts the cockpit, and restores
the previous files automatically if a health check fails.

Updates change the cockpit, SpawnWP CLI, built-in blueprints and templates used by new
environments. They do not rewrite existing spawned projects, `.env` files, credentials,
custom blueprints, WordPress content or databases.

## Updating a WordPress environment

Update a site's WordPress/images safely:

```bash
cd /srv/<site>
make snapshot            # always snapshot first
docker compose pull      # pull updated base images
docker compose build --pull php   # rebuild the PHP image with the latest WordPress
make up
```

New sites always build a fresh image, so they get the latest WordPress automatically.

## Responding to PHP2Shell

PHP2Shell (CVE-2026-109618 and CVE-2026-109619) affects PHP runtimes serving web requests.
For upstream PHP releases, the fixed versions are 8.2.35, 8.3.36, 8.4.27 and 8.5.12. A
distribution may backport a fix without changing the displayed upstream version; verify those
packages against the vendor advisory. See [Security](security.md) for general runtime boundaries.

1. Open **Manage** and review the PHP security warning on each site. The Cockpit inspects the
   running PHP-FPM and Adminer binaries; an image tag alone is not proof of its patch level.
2. Open **System → PHP images**. Use **Prepare update** for each vulnerable PHP series and pinned
   WordPress image shown in the site card. This pulls the current base and builds an image; running
   containers remain unchanged.
3. Apply the new image to each affected site by switching to the same PHP series or recreating its
   PHP service after taking a snapshot. Verify the site's reported PHP version and health.
4. Open **System → Adminer image**, prepare the latest image, then use **Update Adminer** on each
   affected site. The Cockpit recreates only Adminer and verifies its PHP version.
5. Confirm the warning clears for every internet-facing site. If immediate remediation is not
   possible, follow current PHP and national CSIRT guidance on restricting public exposure.

PHP 7.4 and other unsupported branches have no verified upstream fixed release in this product.
SpawnWP permits PHP 7.4 only after explicit acceptance for the exact cached image. That exception
is for a non-internet-facing LAB that is isolated from public and untrusted networks. The product
does not verify network isolation; the operator must enforce and check it independently.

## Long-term hardening checklist

SpawnWP sites are permanent by default, while sites with an explicit lifetime are removed by
the expiry timer. If a site will stay online for months, use the following operating baseline:

1. Keep the host's automatic security updates enabled, or patch the host on a documented schedule.
2. Keep SpawnWP updated and review release notes before installing a control-plane update.
3. Take a named snapshot before WordPress, plugin, theme or image updates.
4. Copy important snapshots and the credentials report off the server; local snapshots are for
   quick rollback, not disaster recovery.
5. Check `certbot certificates`, disk usage and service logs regularly.
6. Review cockpit authentication events and remove credentials or recovery material that is no
   longer needed.
7. Periodically restore a snapshot on a disposable copy so the recovery process is known to work.

Long-term operation still follows the security boundaries in [Security](security.md): SpawnWP
does not provide managed hosting, DNS or email, and the host should not contain unrelated
production workloads.

## TLS certificate

certbot renews the SAN certificate (covering `DOMAIN` and `COCKPIT_DOMAIN`) automatically
via its systemd timer, and reloads nginx on renewal.

```bash
certbot certificates     # see names + expiry
certbot renew --dry-run  # test renewal
```

## Logs

```bash
# A site's container logs
cd /srv/<site> && make logs

# WordPress debug log
cd /srv/<site> && make logs-wp

# Cockpit app
journalctl -u wp-cockpit -f

```

## Backups

- `make snapshot` (or the cockpit's **Snapshot** button) writes to
  `/srv/<site>/backups/db/` and `…/files/`. The last 10 DB snapshots are kept.
- These are **local** backups, good for quick rollbacks. For disaster recovery, copy the
  snapshot files (and your credentials) off-box on a schedule.

## Disk

```bash
cd /srv/<site> && make disk     # host disk + this site's volumes + backups
```

The cockpit's **📊 Disk** button shows a per-site footprint breakdown.
