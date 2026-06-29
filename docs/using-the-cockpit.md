---
description: Use the SpawnWP cockpit to create, manage, inspect, snapshot and destroy isolated WordPress environments.
---

# Using the cockpit

Once you're [in the cockpit](accessing-the-cockpit.md), navigation is split into two
focused pages:

- **Manage** (`/manage`) monitors existing environments and contains their operational
  controls. It is the default page.
- **Deploy** (`/deploy`) creates local environments from blueprints.

In the cockpit, **Deploy** always means “create an environment inside this SpawnWP
lab.” It does not publish that environment to another server or WordPress installation.

Both pages show the same live host summary: circular RAM, disk and normalized load KPIs,
plus numeric uptime.

## The dashboard

- **Resource summary** — host RAM, disk, load average and uptime, refreshed live.
- **Active sites** — one card per site with status, live per-container CPU/RAM, and
  action buttons.

Each site card links directly to both the public front page and **WP Admin**. The latter
opens `wp-admin` in a new tab; use **WP credentials** on the same card when you need the
generated username or password.

There is no manual refresh control. Host metrics update every four seconds, project state
updates every 30 seconds, and completed actions trigger an immediate refresh.

## Create a site

Open **Deploy**, select a blueprint from the catalog, enter a name (lowercase letters,
digits and hyphens only; spaces are not valid in the generated URL), choose an allowed
PHP version, then click **Create site**. Development
is the default. See [Blueprints](blueprints.md) for the Clean, Demo and custom profiles.
The page shows the resolved destination and streams creation output without reloading.
SpawnWP will:

- allocate free internal ports,
- write the site's config and a fresh `.env` with random secrets,
- build/start the container stack and install WordPress,
- validate and apply the selected blueprint,
- add the nginx routes (WordPress on `DOMAIN`, Adminer/Mailpit on `COCKPIT_DOMAIN`).

The result is live at `https://DOMAIN/<name>/`. On completion, Deploy links directly to
the front page, WP Admin and the environment on Manage.

## Per-site actions

Each site card has:

| Control | What it does |
|---|---|
| **▶ Up** / **■ Down** / **↺ Restart** | Start, stop, or restart the whole stack |
| **💾 Snapshot** | Back up the database **and** uploads (timestamped) |
| **🕘 Restore** | List snapshots and roll the site back to one |
| **📊 Disk** | Show the site's real disk footprint (volumes, files, layers) |
| **🗄 DB ▸** | Open Adminer, **already logged in** to this site's database |
| **✉️ Mailpit ▸** | Open this site's captured-mail inbox |
| **🔑 WP credentials** | Reveal the WordPress admin user/password (with copy) |
| **PHP ▾** | Switch this site's PHP version (7.4 legacy / 8.2 / 8.3 / 8.4) |
| **🗑 Destroy** | Permanently delete the site (enabled only when it's Down) |

### Container status and controls

The service table shows the status and live CPU/RAM use of every container in the site
stack: nginx, PHP, MariaDB, Mailpit and Adminer (plus any optional services). The two
icon buttons at the end of each row act only on that container:

| Control | What it does |
|---|---|
| **Restart** (circular arrow) | Restart that service without restarting the rest of the site |
| **Logs** (document icon) | Show the latest 100 log lines from that service in the card's output box |

Use these controls to restart PHP after a configuration change, inspect a failing
database health check, or read Mailpit/nginx errors. The larger **Up**, **Down** and
**Restart** buttons below the table act on the site's entire container stack.

The first switch to a PHP version downloads and compiles its image and can take several
minutes. The cockpit shows structured progress and keeps the verbose BuildKit log under
**Show technical details**. Cached PHP versions switch substantially faster.

### Snapshots & restore

**Snapshot** saves a gzipped database dump plus a tar of `wp-content/uploads`. The last
10 database snapshots are kept automatically. **Restore** lists them (with size and a 📦
marker when uploads are included) and, after a confirmation, overwrites the site with the
chosen snapshot.

### Destroy

Destroy is intentionally guarded: it's only enabled when the site is **Down**, and it
asks for a typed confirmation of the site name. It removes the containers and volumes,
the `/srv/<name>` directory, and the site's nginx blocks on both domains.

### Database & email

- **DB ▸** opens [Adminer](https://www.adminer.org/) on the cockpit subdomain, pre-
  authenticated to the site's database (a same-origin bridge fills in the login).
- **Mailpit ▸** opens [Mailpit](https://mailpit.axllent.org/), which captures every
  email the site sends so you can inspect password resets, notifications, etc., without
  delivering real mail. Each site has a separate inbox, including message content,
  HTML rendering, headers and attachments. Messages are stored in that site's Mailpit
  volume until you delete them in Mailpit or destroy the environment.

Mailpit is a development inbox, not an email provider: messages shown there are **not**
forwarded to their real recipients. This prevents test password resets, WooCommerce
notifications and plugin emails from reaching actual users.

## The Dev toolkit (inside WordPress)

Sites created from the **Development** blueprint show a **🛠 Dev toolkit** widget with quick links to
Plugin Check, Theme Check, Query Monitor, WP Crontrol and User Switching, plus the CLI
commands for phpcs and phpstan. The dashboard is decluttered to just **Site Health** and
the Dev toolkit. Clean and Demo sites intentionally omit it. See
[WordPress development](wordpress-development.md).

## When the system is busy

While an image is building (e.g. a first PHP-version switch), the cockpit shows a banner
and temporarily disables "sensitive" actions to avoid instability. Read-only views stay
available. This is automatic.
