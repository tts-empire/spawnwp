# SpawnWP Deploy development

The distributable files in this directory track the latest stable release published at
<https://wordpress.org/plugins/spawnwp-deploy/>. The `main` branch must never contain a
public `-dev` build: work on the next plugin version in a dedicated branch and merge it
only after WordPress.org exposes the matching stable tag and ZIP.

SpawnWP Deploy supports two administrator-initiated workflows:

- capture a configured WordPress site as a reusable blueprint on a self-hosted SpawnWP
  server;
- publish a finished site once to a separate, fresh WordPress installation.

It is deliberately not a staging or continuous synchronization engine. The complete user
documentation, requirements, external-service disclosure and changelog live in
`readme.txt`, which is also the WordPress.org directory readme.

## Repository layout

The WordPress.org package consists only of:

- `spawnwp-deploy.php`
- `readme.txt`
- `assets/`
- `src/`
- `recovery/`

Development-only files (`README.md`, `bin/`, `tests/`, `dist/` and
`release-public.pem`) must never enter the plugin ZIP. The public key is used by SpawnWP
to verify its signed compatibility mirror; it is not used as a custom WordPress updater.

## Validation

Build a deterministic local ZIP with:

```bash
./bin/build-release.sh /root/.spawnwp/deploy-release-ed25519.pem
```

Before a WordPress.org release, run PHP lint, JavaScript syntax checking, WPCS,
PHPCompatibilityWP, Plugin Check and the smoke test on a clean WordPress installation:

```bash
wp eval-file wp-content/plugins/spawnwp-deploy/tests/smoke.php
```

The release branch is copied to SVN `trunk`, tagged with the same semantic version, and
published only after `Stable tag`, the plugin header and the version constant agree. Once
the official API exposes that version, the SpawnWP mirror synchronizes and signs the exact
WordPress.org ZIP automatically; only then is the release branch merged to `main`.
