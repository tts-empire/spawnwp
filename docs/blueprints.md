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
