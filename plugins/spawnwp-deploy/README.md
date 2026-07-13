# SpawnWP Deploy

Development status: **public preview**.

SpawnWP Deploy publishes a SpawnWP site once to a fresh, empty WordPress installation.
It is deliberately not a staging/synchronization engine.

Since 0.2.0 it can also **capture a configured site as a content blueprint** on the
owner's SpawnWP server (0.4.0+): pair with a single-use code generated in the
cockpit, choose what to capture (plugin files, themes, uploads, database — users and
passwords never included), and the capture is pushed as an Ed25519-signed chunked
upload to `/api/ingest/*`.

Download the preview package and verification files from
<https://spawnwp.com/deploy/>. The plugin is optional and is not installed by SpawnWP.

## Requirements

- WordPress single-site, same exact core version on source and target
- Same PHP major/minor on source and target
- PHP 7.4+, sodium and zip extensions
- HTTPS with a publicly trusted certificate
- WordPress REST API reachable without an additional HTTP password
- Direct write access to wp-content, plugins and themes
- Local uploads and free disk space for staging/rollback

## Build

```bash
./bin/build-release.sh /secure/path/to/ed25519-private.pem
```

Artifacts are written under `dist/`: ZIP, SHA-256 file and a base64 Ed25519 signature
of the SHA-256 file.
The signing key must never be stored in this repository.

## Smoke test

Install and activate the plugin in a disposable WordPress environment, then run:

```bash
wp eval-file wp-content/plugins/spawnwp-deploy/tests/smoke.php
```

The smoke test does not activate a deployment. End-to-end activation requires a fresh,
empty target and must be tested with rollback enabled.

## Explicit non-goals for v1

- deploying to an existing or transactional site
- repeated pushes
- merging users, orders, comments or form submissions
- WordPress Multisite
- remote object-storage uploads
- staging sync or blueprints

## Changelog

### 0.3.4-dev

- **The blueprint capture form remembers what you last captured.** Re-pushing an update to
  an existing blueprint no longer means retyping the id, name, description and capture
  options: they are pre-filled from your previous capture on that server, with the patch
  version bumped (`1.4.9` → `1.4.10`). This matters beyond convenience — the documented
  workflow is to re-capture with the *same id* to **replace** a blueprint, and a mistyped
  id silently forks a new one instead.
  The fields are remembered per SpawnWP server connection, and are saved even when a
  capture then fails (an oversized payload, an unreachable server), which is exactly when
  retyping hurts most. A first-ever capture is unchanged. A *Start a new blueprint* button
  clears the form when you want a different blueprint rather than a new version of the
  same one. Requested by [@wpeasy](https://github.com/wpeasy) in
  [discussion #9](https://github.com/tts-empire/spawnwp/discussions/9).

### 0.3.3-dev

- **Kind-aware package exclusions.** Directories named `Upgrade/`, `Cache/` or `Backup/`
  inside plugin *source* trees are legitimate code and are now kept, while the same names
  are still stripped from user uploads. Fixes a fatal on sites deployed with MetaBox AIO,
  whose `MBB\Upgrade\Manager` class was being dropped from the package.

### 0.3.2-dev

- **Capture the source WordPress version.** A captured blueprint now records the origin
  site's exact WordPress version (e.g. `6.5.2`) instead of always `latest`, so sites
  spawned from it reproduce the origin. The spawn can still override it to `latest` from
  the cockpit. Beta/RC suffixes are reduced to the numeric core; an unreadable version
  falls back to `latest`.

### 0.3.1-dev

- **PHP 7.4 support.** The minimum PHP requirement drops from 8.1 to 7.4 so the plugin
  can run on the older WordPress hosts it is deployed to. The code used no PHP 8.x
  syntax; the only 8.0 dependencies were `str_starts_with`/`str_contains`, now covered
  by guarded polyfills (in addition to the WordPress core polyfills available since
  5.9). Verified with `php -l` and the smoke test on WordPress 6.8 / PHP 7.4.
