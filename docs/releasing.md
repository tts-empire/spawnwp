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
installed server with `spawnwp update --check`; then test update and rollback on a staging
VPS before announcing it.

## Repository transition

While the repository is private, update checks use the server's GitHub CLI credentials.
When it becomes public, clients work anonymously without configuration. At that point,
enable GitHub Pages, restore the docs workflow's `push` trigger, and enable `main` branch
protection with the `validate` check required.
