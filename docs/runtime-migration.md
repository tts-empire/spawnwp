# Debian 13 runtime migration

SpawnWP uses separate containers: Debian 13 PHP-FPM, vendor Alpine images for
Nginx/Adminer/Mailpit/Redis, and the existing MariaDB 10.11 image. The host operating
system is independent of those images.

## Preparing and applying

System → Refresh prepares a verified image without changing running sites. From
a site's directory the equivalent is:

```sh
python3 scripts/prepare-image.py --project . --version 8.4 --wordpress latest --refresh
```

Choose the prepared PHP version in Manage, or run `make php-switch VER=8.4`.
The site keeps its actual WordPress core and data. `PHP_IMAGE_REF` identifies the
selected immutable image. Compose does not build or pull missing PHP images
implicitly. A deleted selected image must be prepared/recovered before Up.

For PHP 7.4, prepare the image first and review the exact image ID in System.
Use image-specific LAB acceptance in the cockpit, or pass both
`ACCEPT_VULNERABLE=1` and `ACCEPTED_IMAGE_ID=sha256:...` to the CLI. Verified
backports do not restore upstream support to this EOL branch.

The release migration stages recognized managed files and pins the images already
selected by each site. It never starts a Down site or recreates a running service.
If files differ from the supported managed baseline, the site is flagged for
review and its configuration is preserved. The first supported baseline is 0.5.52.

Nginx has a separate **Update Nginx** action. Refreshing and updating Adminer also
remain separate operations. Both retain their prior exact image for rollback.

## Backup and rollback

Before a site's first runtime migration, `scripts/backup-runtime.py` saves a private
backup under `backups/runtime/`: configuration, full WordPress files, the database
and consistent Mailpit SQLite data. Running PHP is briefly paused during capture;
Down sites use consistent offline volume archives instead of starting services.
Only directories containing `complete.json` are complete migration backups.

A failed apply restores the previous runtime reference and checks readiness again.
For a later manual PHP rollback, run `python3 scripts/rollback-runtime.py --project .`.
An EOL image additionally requires `--accept-legacy --accepted-image-id sha256:...`.
This changes only the PHP selection, preserving newer settings and application data.
`.spawnwp/runtime-rollback.json` records the last environment and prior image;
`.spawnwp/runtime-history.json` protects earlier image references for 14 days.
These files contain private operational state and must remain root-readable only.
Image garbage collection also protects images selected by Down sites.

Restoring application data is a separate operation. A runtime-only rollback must
not overwrite newer database changes or uploads. A raw offline database archive
must be restored with MariaDB stopped, using the same database image version.

## Validation and promotion

The Runtime images workflow builds all five PHP branches on native amd64 and
arm64 runners, with latest and pinned WordPress variants. It starts isolated
Compose projects with the actual security/resource limits and tests FPM/CLI,
WordPress, MariaDB, Redis, Mailpit, Adminer, backup/restore, INI overrides, Xdebug and
standard/imported blueprints. Reports are uploaded as CI artifacts.

Before publication, install the signed candidate on the development Cockpit using
the local-bundle update path. Confirm the Cockpit health check passes, the existing
environment remains healthy with its selected images unchanged, and the runtime
migration review status is visible. A legacy WP-Cron correction recreates a running
PHP container only when its live environment still disables WP-Cron; its prior image
is pinned before that operation. Image
builds, all PHP transition pairs and the warm-deploy comparison are covered by the
native amd64/arm64 CI workflows above.

The migration never switches an existing environment to another PHP version
automatically. Each site owner chooses when to prepare and apply a runtime; the site
backup and rollback path protect that explicit change. There is no fixed staging soak or
three-clone release gate for this runtime update. Follow normal Cockpit monitoring
after publication and use the documented rollback path if a site reports a problem.

## Updating locked inputs

`docker/php/runtime-lock.json` records base image digests, both architectures' Sury
package versions/checksums, and the key identity. The download lists must match
that manifest. Changes to legacy core packages also require reviewing the source
changelog/patches for each CVE and updating the exact allowlist in php_security.py.
Do not infer legacy patch status from PHP_VERSION alone.

Tool downloads use tools-lock.json; Composer uses a committed lock. Build inputs
are enumerated in scripts/lib-image.sh. Per-site INI and debugger toggles are
excluded, so changing settings never invalidates the shared build cache.

## Adminer 6 updates (SpawnWP 0.5.54)

New sites select the verified Adminer 6 candidate prepared on the server, or the
release-pinned Adminer 6.3.0 baseline. In System, **Prepare Adminer update** follows
only the 6.x series and checks the Adminer version and the bundled PHP patch level.
The System page shows the prepared versions. It does not change existing sites.

On each site's Manage card, choose **Update Adminer**. Cockpit displays the actual
Adminer application version separately from PHP. The action is available even when
PHP is already patched. A stopped service receives a selection for its next start;
it is not started by the update action.

Older standard Compose files with a literal `image: adminer:5` are converted to use
`ADMINER_IMAGE_REF` when you apply the update. Only that image declaration changes.
Custom images, unrecognized declarations or environment/Compose overrides that prevent
the prepared image from being selected produce an error and require manual review.
Unrelated customized PHP configuration does not have to be replaced to update Adminer.

The updater compares the actual container image ID against the prepared image for the
server's architecture, then verifies PHP and HTTP. It saves the previous configuration
and exact image in `.spawnwp/adminer-rollback.json`, retaining the image for 14 days.
A failed apply automatically restores the previous image. The runtime files preserve
these private operational records; do not publish them.

For a later manual rollback, use the `environment` and `previous_ref` saved in that
record, preserving any unrelated settings changed since the update. Set only
`ADMINER_IMAGE_REF` to `previous_ref` in the site's `.env`, then run
`docker compose up -d --no-deps --no-build --force-recreate adminer`. Confirm the image
ID and HTTP availability. If Adminer was stopped, update only the selection and leave
it stopped. This does not restore WordPress files or database data.
