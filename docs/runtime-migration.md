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

Before release promotion also complete host installation/update checks, all PHP
transition pairs, repeated warm-deploy benchmarks (no unexplained median regression
above 15%), the plugin transfer fixture, and 24 hours of observation on dedicated
legacy/modern/imported clones. Those checks require staging capacity; they are not
implied by a successful image build or unit-test run.

## Updating locked inputs

`docker/php/runtime-lock.json` records base image digests, both architectures' Sury
package versions/checksums, and the key identity. The download lists must match
that manifest. Changes to legacy core packages also require reviewing the source
changelog/patches for each CVE and updating the exact allowlist in php_security.py.
Do not infer legacy patch status from PHP_VERSION alone.

Tool downloads use tools-lock.json; Composer uses a committed lock. Build inputs
are enumerated in scripts/lib-image.sh. Per-site INI and debugger toggles are
excluded, so changing settings never invalidates the shared build cache.
