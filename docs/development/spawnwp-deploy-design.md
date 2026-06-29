---
description: Internal design notes for the unreleased SpawnWP Deploy WordPress transfer plugin.
---

# SpawnWP Deploy design (unreleased)

> Internal design document. Do not add this page to the public MkDocs navigation until
> the implementation and compatibility matrix are verified.

SpawnWP Deploy performs a one-time publication from a SpawnWP environment to a fresh,
empty WordPress installation. Both sites run the same plugin and pair with a short-lived
connection bundle. Each connection has independent Ed25519 keys and can be revoked from
either side.

The source packages the application database, plugins, themes and local uploads. It does
not ship WordPress core, users, wp-config, server configuration, MU plugins, drop-ins or
SpawnWP's development-only tools. The receiver preserves its URL, administrators and
deployment control data, stages and verifies the payload, then enters a short maintenance
window for activation. A pre-deploy rollback is retained for seven days.

The receiver refuses a non-empty or previously deployed target. This makes v1 a safe
initial publication tool, not staging. Repeated pushes, selective synchronization,
live-data merge and blueprint workflows remain roadmap work.

Security and compatibility requirements:

- HTTPS with a valid certificate;
- signed requests with timestamp, nonce and body hash;
- resumable, checksummed binary chunks;
- WordPress single-site and matching core/PHP major-minor versions;
- direct filesystem writes, local uploads and sufficient temporary disk;
- no unsupported foreign keys, triggers, views, routines or symlinks;
- automatic rollback on failed activation/health verification.

Implementation references:

- https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/
- https://developer.wordpress.org/apis/filesystem/
- https://teamupdraft.com/documentation/updraftcentral/getting-started/how-to-add-a-site-to-updraftcentral/
