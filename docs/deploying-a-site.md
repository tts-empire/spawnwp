# Deploying a site

SpawnWP Deploy is an optional plugin for publishing a site once from a SpawnWP
environment to a **fresh, empty WordPress installation**. It is a convenience for
initial publication, not a required deployment strategy and not a staging or
synchronization service.

!!! warning "Public preview"
    The plugin is currently `0.1.0-dev`. Test it with disposable sites and keep an
    independent backup of the target. Do not use it for a site that already contains
    content or live user data.

## Requirements

The source and target must both use:

- WordPress single-site with the exact same WordPress core version;
- the same PHP major and minor version, with PHP 8.1 or newer;
- the `sodium` and `zip` PHP extensions;
- HTTPS with a publicly trusted certificate;
- a reachable WordPress REST API without an additional HTTP password;
- direct, writable local `wp-content`, plugins, themes and uploads;
- enough free disk space for staging and rollback.

The transfer limit is 2 GiB. Multisite, object-storage uploads, database objects such
as triggers or views, and symlinked content are not supported.

## Install and connect

1. [Download SpawnWP Deploy](https://spawnwp.com/deploy/) and install the same ZIP on
   the source and target from **Plugins → Add New → Upload Plugin**.
2. Activate it on both sites and open **Tools → SpawnWP Deploy**.
3. On the fresh target, generate a connection key. It expires after 15 minutes.
4. Paste that key into the source and run the preflight checks.
5. Review the result and explicitly confirm the deployment.

The source packages the database, plugins, themes and local uploads. WordPress core,
users, `wp-config.php`, MU plugins, drop-ins and SpawnWP development tools are excluded.
The target preserves its URL, administrator accounts and deployment control data.

## Transfer and recovery

Requests are signed with per-connection Ed25519 keys and protected with timestamps,
nonces and body hashes. Payload chunks are checksummed and resumable. The target stages
and verifies the package before entering maintenance mode, then performs a health check.
If activation fails it rolls back automatically. The pre-deployment rollback is kept
for seven days.

## What it does not do

SpawnWP Deploy does not provide repeated pushes, selective synchronization, live-data
merging, staging workflows or deployment to an existing site. You can use any other
deployment strategy that suits your project.
