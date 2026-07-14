---
description: Compare ways to publish or export a finished SpawnWP site, including the optional SpawnWP Deploy WordPress plugin.
---

# Publishing or exporting a finished site

SpawnWP does not decide how you publish a finished WordPress site. You can create a
backup, use a migration plugin, export content, copy files and databases manually, or
use any deployment workflow that suits the project.

The tool described on this page is **SpawnWP Deploy**, an optional WordPress plugin.
It is not part of the SpawnWP installer, it is not installed automatically, and it is
not required to create or use environments in the cockpit.

## Keep these actions separate

| Action | What it means |
|---|---|
| **Install SpawnWP** | Set up the self-hosted lab and cockpit on your server. |
| **Deploy in the cockpit** | Create a disposable WordPress environment inside your SpawnWP lab. |
| **Publish or export a finished site** | Move finished work somewhere else using a method you choose. |

SpawnWP Deploy applies only to the third action. It offers one guarded transfer from a
source WordPress site inside SpawnWP to a **separate, fresh and empty WordPress
installation**.

!!! warning "Optional public-preview plugin"
    The plugin is currently `0.3.1-dev`. Test it with disposable sites and keep an
    independent backup. Do not use it for a destination that already contains content
    or live user data.

Since `0.3.0-dev` the plugin leads with **blueprints** — capturing a configured site as
a reusable template (see [Content blueprints](blueprints.md#content-blueprints-captured-from-a-site)).
Publishing a finished site out, described below, is its secondary role: it is offered
only on sites running inside a SpawnWP cockpit, and on an external destination the
plugin instead offers to *receive* a published site.

!!! tip "Bringing an existing site into SpawnWP?"
    If your goal is to pull a WordPress site you already run into your SpawnWP lab, follow
    the step-by-step [Import an existing WordPress site](importing-a-site.md) guide — it
    captures the site as a reusable blueprint you can spawn fresh environments from.

## When this plugin fits

Use SpawnWP Deploy when all of these are true:

- you want a one-time transfer rather than continuous synchronization;
- the destination is a separate, fresh WordPress installation;
- source and destination are WordPress single-site;
- both use the exact same WordPress core and PHP major/minor versions;
- both have PHP 7.4+, `sodium`, ZIP, trusted HTTPS and a reachable REST API;
- both use directly writable local plugins, themes and uploads.

The transfer limit is 2 GiB. Multisite, object-storage uploads, database triggers or
views, and symlinked content are not supported. Choose another migration method when
these constraints do not fit your project.

## Install the WordPress plugin

On a site created inside SpawnWP, the quickest way is to tick **Install the SpawnWP
Deploy plugin** on the cockpit's Deploy page when you create the site — it arrives
installed and activated from the latest signed release (SpawnWP verifies its Ed25519
signature; if the server is offline it falls back to the signed copy bundled with
SpawnWP). Otherwise install it by hand:

1. [Download the optional plugin](https://spawnwp.com/deploy/).
2. In the source site inside SpawnWP, open **Plugins → Add New → Upload Plugin**,
   upload the ZIP and activate it.
3. Repeat the same WordPress plugin installation on the separate, fresh target site.
4. Open **Tools → SpawnWP Deploy** in both WordPress dashboards.

Installing the plugin does not start a transfer.

## Connect and transfer

1. On the target WordPress site, generate a connection key. It expires after 15 minutes.
2. Paste the key into the source WordPress site and run the compatibility checks.
3. Review the result and explicitly confirm the transfer.

The source packages the database, plugins, themes and local uploads. It does not copy
WordPress core, users, `wp-config.php`, MU plugins, drop-ins or SpawnWP development
tools. The target keeps its own URL and administrator accounts.

Requests use per-connection Ed25519 signatures, timestamps, nonces, body hashes and
checksummed resumable chunks. The target stages and verifies the package before
activation, performs a health check and rolls back automatically if activation fails.
The pre-transfer rollback is retained for seven days.

## Capture a site as a blueprint

The plugin's main job, since `0.3.0-dev`, is to capture the configured site as a
reusable **content blueprint** on your own SpawnWP server: pair with a single-use code
from the cockpit's **System → Blueprint capture**, choose what to capture, and press
*Create blueprint*. See
[Content blueprints](blueprints.md#content-blueprints-captured-from-a-site) for the
full flow, defaults and privacy guarantees.

## What the plugin does not replace

SpawnWP Deploy is not a backup system, hosting service, staging platform or general
synchronization tool. It does not provide repeated pushes, selective synchronization,
live-data merging or publication to an existing site.

If a backup, another migration plugin, Git-based workflow or manual process is a better
fit, use that instead. SpawnWP environments do not depend on this plugin.
