# SpawnWP Deploy

Development status: **unreleased**.

SpawnWP Deploy publishes a SpawnWP site once to a fresh, empty WordPress installation.
It is deliberately not a staging/synchronization engine.

## Requirements

- WordPress single-site, same exact core version on source and target
- Same PHP major/minor on source and target
- PHP 8.1+, sodium and zip extensions
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
