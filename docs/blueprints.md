---
description: Learn how SpawnWP blueprints define repeatable WordPress development environments and what is currently implemented.
---

# Blueprints

Blueprints define the WordPress state applied when SpawnWP creates a site. They do not
change the container architecture: every site still receives isolated nginx, PHP,
MariaDB, Mailpit and Adminer services.

## Built-in blueprints

| Blueprint | Result |
|---|---|
| **Development** (default) | Empty site with `WP_DEBUG`, Plugin Check, Theme Check, Query Monitor, WP Crontrol, User Switching and the Dev toolkit |
| **Clean** | Empty WordPress without sample content, development plugins, Dev toolkit or debug mode |
| **Demo** | Presentable starter with Home, About and Contact pages, a static homepage and a Primary menu |

Choose the blueprint and PHP version from the catalog on the cockpit's **Deploy** page.
Here, Deploy means creating a local environment inside the SpawnWP lab, not publishing
a finished site elsewhere.
The blueprint controls
which PHP versions it permits. WordPress is `latest` in schema v1.

Each site stores the exact resolved manifest at:

```text
/srv/<site>/.spawnwp/blueprint.json
```

The cockpit displays its blueprint ID and version. Updating a blueprint affects only
sites created afterward; SpawnWP never reapplies it automatically to an existing site.

## Custom blueprints

Place custom JSON manifests in `/etc/spawnwp/blueprints.d/`. The filename must match the
blueprint ID, for example `plugin-review.json`:

```json
{
  "schema_version": 1,
  "id": "plugin-review",
  "name": "Plugin review",
  "version": "1.0.0",
  "description": "A focused environment for reviewing a WordPress.org plugin.",
  "php": {"default": "8.3", "allowed": ["7.4", "8.2", "8.3", "8.4"]},
  "wordpress": "latest",
  "debug": true,
  "plugins": ["plugin-check", "query-monitor"],
  "theme": null,
  "devkit": true,
  "content_preset": "empty"
}
```

Plugin and theme values must be WordPress.org slugs. Supported content presets are
`empty` and `demo`. Unknown fields, duplicate IDs, arbitrary URLs and executable hooks
are rejected. Invalid custom manifests are ignored and reported by the cockpit without
hiding valid blueprints.

## Content blueprints (captured from a site)

!!! tip "Looking for a walkthrough?"
    This section is the reference for how capture works. For a goal-oriented, step-by-step
    guide, see [Import an existing WordPress site](importing-a-site.md).

Since 0.4.0 a blueprint can also be **captured from an already-configured WordPress
site** with the [SpawnWP Deploy plugin](deploying-a-site.md) — this is the plugin's
primary job since `0.3.0-dev`. On a site created inside SpawnWP you can add the plugin
by ticking *Install the SpawnWP Deploy plugin* when you create the site; on your own
external WordPress, install it by hand. Then:

1. On the cockpit's **System** page, under **Blueprint capture**, generate a
   pairing code (single-use, valid 15 minutes) and paste it into the plugin's
   *Create a SpawnWP blueprint from this site* panel on the source site.
2. Choose what to capture — plugin files, theme files, media uploads and the
   database, all enabled by default — and press **Create blueprint**. The payload
   (up to 2 GiB) is pushed to the server in signed, checksummed chunks.
3. The blueprint appears under **Your blueprints** on the **Deploy** page, with its
   size and a capture summary, and behaves like any other blueprint.

### Spawning from a captured blueprint

Two things are specific to sites created from a captured blueprint:

- **A fresh administrator is created.** The capture never includes the source site's
  user accounts or passwords (the `users` and `usermeta` tables are excluded), so the
  new site has only its own new admin — use the credentials shown under **🔑 WP
  credentials** on the Manage page, not the source site's login.
- **Plugins start deactivated by default.** A captured site can carry security or
  login plugins (IP allow-lists, passwordless login, lockouts) that would otherwise
  lock you out of the fresh site. On the Deploy page, *Start with all plugins
  deactivated* is checked by default: the plugins are installed but inactive, and an
  admin notice in the new site reminds you to **reactivate them one at a time** so a
  problematic one is easy to spot. Uncheck it to start with them active, as captured.

Content blueprints use manifest **schema v2** (`schema_version: 2`), are accepted
only from `/etc/spawnwp/blueprints.d/`, and reference a payload archive under
`/var/lib/spawnwp/blueprints/<id>/`. The manifest and payload are installed
atomically: an interrupted capture never leaves a half-installed blueprint, and
re-capturing with the same id swaps the payload only after full verification.

Defaults and guarantees:

- **Users and passwords are never captured** (the `users` and `usermeta` tables are
  excluded); each spawned site keeps its own fresh admin, and captured content is
  reassigned to it.
- The database capture includes the source site's real posts, pages and settings —
  the plugin asks for explicit confirmation, because that content will appear in
  every site spawned from the blueprint.
- The source site URL is rewritten to a fixed placeholder before upload and never
  reaches the server; every spawned site rewrites the placeholder to its own URL.
- Plugins that are not from WordPress.org are listed in the manifest and flagged at
  capture time and on the Deploy card: spawned sites may require new license keys or
  re-activation for them. If the payload exceeds 2 GiB, exclude the uploads and
  re-capture.
- The allowed PHP versions are chosen at capture time; the default pins to the
  source site's PHP version.

The payload never leaves your server and is not part of telemetry. Content
blueprints can be deleted — manifest and payload — from **System → Content
blueprints**; existing sites are unaffected.

Validate the catalog from the server with:

```bash
/srv/wp-dev/scripts/blueprint.py list
/srv/wp-dev/scripts/blueprint.py resolve plugin-review --php 8.4
```

PHP 7.4 is available only as a legacy compatibility runtime for retrofitting and
testing old code. It is end-of-life, must not be a blueprint default, and should
not be used for new or public production sites. SpawnWP still installs the current
WordPress release in this runtime rather than the obsolete core bundled with the
old upstream PHP image.

!!! warning
    Installing a plugin or theme is part of the site transaction. If WordPress.org is
    unavailable or a slug cannot be installed, creation fails and SpawnWP removes the
    partial containers, volumes, directory and nginx routes.
