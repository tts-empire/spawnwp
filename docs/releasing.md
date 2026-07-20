---
description: Maintainer workflow for validating, signing and publishing a SpawnWP release.
---

# Releasing SpawnWP

Releases are stable SemVer tags and immutable GitHub Release assets. The repository is
`tts-empire/spawnwp`; prereleases are not accepted by the updater.

## Release key

The signing key currently lives at `/root/.config/spawnwp/release-private.pem`, mode
`0600`. It is never committed or uploaded to GitHub. Before public release, move release
signing to an offline workstation and keep an encrypted offline backup. The corresponding
public-key SHA-256 fingerprint is:

```text
138fd0e7af98e0c2ed7afb7bba1a7fb5fe85939b0c1661cca90f9bd4f3b47e36
```

## Publish a stable release

1. Update `VERSION`, `docs/changelog.md` and `docs/release-notes/X.Y.Z.md`.
2. Run the test workflow locally and ensure the working tree is clean.
3. Create and push the annotated `vX.Y.Z` tag from the release commit.
4. Build, sign and upload the immutable assets:

```bash
git tag -a vX.Y.Z -m "SpawnWP X.Y.Z"
git push origin main vX.Y.Z
SPAWNWP_RELEASE_KEY=/secure/path/release-private.pem \
  bash updater/publish-release.sh X.Y.Z
```

The publishing script refuses to replace an existing release. Verify the release from an
installed server with `spawnwp update --check`; then test update and rollback on a clean
staging server before announcing it.

## Release SpawnWP Deploy

WordPress.org is the only stable release authority for the optional plugin. The
distributable tree under `plugins/spawnwp-deploy/` on `main` must match the current
WordPress.org ZIP; develop the next version on a dedicated branch.

1. Update the plugin header, version constant, `readme.txt` Stable tag and changelog on
   the release branch.
2. Run PHP lint, JavaScript syntax checking, WPCS, PHPCompatibilityWP, Plugin Check and
   the clean-site smoke test.
3. Copy the approved distributable tree to SVN trunk, create `tags/X.Y.Z`, commit and
   complete WordPress.org Release Confirmation if enabled.
4. Wait until the Plugin Information API and official ZIP expose `X.Y.Z`. Do not merge
   the release branch while the API still reports the previous stable.
5. Verify the production mirror and Git source:

```bash
sudo python3 /usr/local/lib/spawnwp/sync_wporg_plugin.py --check
python3 ops/website/sync_wporg_plugin.py \
  --check-source plugins/spawnwp-deploy \
  --lock-file /tmp/spawnwp-plugin-source-check.lock
```

The production timer checks every ten minutes and signs the exact WordPress.org ZIP for
legacy cockpit clients. It never writes to SVN. New cockpit sites prefer this verified
stable mirror and fall back to the stable ZIP embedded in the current SpawnWP release
when offline. Existing WordPress sites update through WordPress core; do not add a custom
plugin updater or force automatic updates.

## Clean-server acceptance gate

A release is not stable until its public installer and signed assets pass on a separate,
fresh server. The development/origin host is not an acceptable substitute. Verify:

- installation, one-time activation, Passkey, TOTP and recovery codes;
- an initially empty cockpit and deployment with every built-in blueprint;
- WordPress, WP Admin, Adminer, Mailpit and generated credentials;
- full-stack and per-container lifecycle actions;
- PHP switching, including expired recent-authentication and inline confirmation;
- snapshot/restore, complete destroy and Nginx rollback after a failed deployment;
- telemetry enable/disable, dashboard update, reboot persistence and rollback;
- `nginx -t`, `certbot renew --dry-run`, and public exposure limited to ports 80/443.

Do not repair the staging server manually. Fix the repository, publish a new signed release,
update the staging server through the product, and repeat the failed test.

## Repository transition

While the repository is private, update checks use the server's GitHub CLI credentials.
When it becomes public, clients work anonymously without configuration. At that point,
enable GitHub Pages, restore the docs workflow's `push` trigger, and enable `main` branch
protection with the `validate` check required.
