# CLI reference

## SpawnWP platform

| Command | Description |
|---|---|
| `spawnwp version` | Print the installed SpawnWP version |
| `spawnwp update --check` | Check the latest stable GitHub Release without changing the server |
| `sudo spawnwp update` | Verify, install and activate the latest stable release |
| `sudo spawnwp update --version X.Y.Z` | Install a specific stable release |
| `sudo spawnwp rollback` | Restore the previous installed SpawnWP release |
| `sudo spawnwp auth reset` | Reset cockpit access and issue a new 24-hour activation code |
| `spawnwp telemetry status` | Show local telemetry consent and expiry |
| `spawnwp telemetry payload` | Print exactly what the next heartbeat would send |
| `sudo spawnwp telemetry disable` | Revoke consent and delete telemetry identity/queue |

Most things are doable from the [cockpit](using-the-cockpit.md), but each site is also a
plain Docker Compose project with a `Makefile`. Run these from the site directory
(`/srv/<site>/`).

## Lifecycle

| Command | Description |
|---|---|
| `make up` | Build (if needed) and start the stack |
| `make down` | Stop the stack |
| `make restart` | Restart all services |
| `make status` | Container status + resource usage |
| `make logs` | Follow all container logs |
| `make bootstrap` | First-run WordPress install (idempotent) |
| `make rebuild` | Force-rebuild the PHP image and restart it |

## PHP versions

| Command | Description |
|---|---|
| `make php-switch VER=8.2` | Switch PHP version (`7.4` legacy, `8.2`, `8.3`, `8.4`); cached versions are instant |

## Development

| Command | Description |
|---|---|
| `make shell` | Bash shell inside the PHP container |
| `make db-shell` | Interactive MariaDB CLI |
| `make wp CMD="plugin list"` | Run any WP-CLI command |
| `make composer CMD="install"` | Run Composer in the PHP container |
| `make npm CMD="run build"` | Run npm in the PHP container |
| `make test` | Run each plugin's test suite (if configured) |
| `make lint` | Run PHPCS on plugins that ship a `phpcs.xml` |

## Xdebug

| Command | Description |
|---|---|
| `make xdebug-on` | Enable Xdebug (port 9003), restarts PHP |
| `make xdebug-off` | Disable Xdebug (default) |

## Backup & restore

| Command | Description |
|---|---|
| `make snapshot` | Database snapshot → `backups/db/` (add `INCLUDE_FILES=1` for uploads) |
| `make restore SNAPSHOT=<timestamp>` | Restore a snapshot (DB + uploads if present) |
| `make reset` | Stop and wipe all volumes (takes a pre-reset snapshot first) |

## Observability

| Command | Description |
|---|---|
| `make mail` | Print the Mailpit URL / SSH-forward command |
| `make logs-wp` | Tail the WordPress `debug.log` |
| `make disk` | Host disk + volumes + backups usage |

## New site

From the internal environment-template directory (`/srv/wp-dev/`):

| Command | Description |
|---|---|
| `make new-project NAME=<slug>` | Spawn a site with the default Development blueprint |
| `make new-project NAME=<slug> BLUEPRINT=clean PHP=8.4` | Spawn with a selected blueprint and allowed PHP version |
