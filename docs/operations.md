# Operations

Day-to-day running of a SpawnWP host.

## Where things live

| Path | Contents |
|---|---|
| `/srv/wp-dev/` | The primary site (and the stack template scripts) |
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
| `docker-prune.timer` | Weekly Docker build-cache prune (safe; never touches volumes) |

## Updating SpawnWP

The cockpit checks the latest stable GitHub Release and shows an **Updates** indicator.
Checks are anonymous once the repository is public and remain independent from telemetry.
Review the release notes, then update explicitly as root:

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
