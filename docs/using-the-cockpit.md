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
- start the container stack and install WordPress (the PHP image is built only the
  first time a PHP version is used or after a SpawnWP update that changes it — that
  one-off build takes about 5 minutes and the cockpit shows a clear notice when it
  happens; every other creation reuses the image and takes about 35 seconds. If you
  chose the image pre-build during installation, even the first PHP 8.3 creation is
  fast. Images never rebuild on a schedule: refresh them when you choose, from
  **System**),
- validate and apply the selected blueprint,
- add the nginx routes (WordPress on `DOMAIN`, Adminer/Mailpit on `COCKPIT_DOMAIN`).

The result is live at `https://DOMAIN/<name>/`. On completion, Deploy links directly to
the front page, WP Admin and the environment on Manage. Blueprint cards show each
profile's approximate deploy time, and the launch bar's **expected time** reflects your
actual selection — it knows whether the chosen PHP version's image is already built.

### Lifetime (temporary sites)

The **Lifetime** field makes a site disposable: pick 1, 3, 7 or 30 days and the site is
**destroyed automatically** when it expires — containers, database, files and routes,
with **no backups kept**. Manage shows a countdown badge on temporary sites and a
**⏳ Lifetime** action to extend them or make them permanent. The default is Permanent.

### PHP settings (advanced)

The Deploy form's collapsed **PHP settings** panel exposes the classic
hosting knobs — `memory_limit`, `upload_max_filesize`, `post_max_size`,
`max_execution_time`, `max_input_vars`, `max_input_time` and a `display_errors` toggle.
Leave it untouched for the defaults (256M / 64M / 64M / 120s / 3000 / -1 / Off). Raising
the upload sizes automatically aligns the nginx limits (site and proxy) so large uploads
actually work, up to 512M. The values live in a per-site override file mounted into the
php container, so they never rebuild the shared image — and they can be changed later
from **Manage → ⚙️ PHP settings** (applies in ~2 seconds with a php restart). Sites
created before 0.3.14 don't have the override mount; recreate them to use this feature.

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
| **📂 Files** | Open the [file browser](#the-file-browser) for this site |
| **⌨ WP-CLI** | Open the [WP-CLI console](#the-wp-cli-console) for this site |
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
**Show technical details**. Cached PHP versions switch substantially faster. The same
applies to site creation: once a PHP version's image exists, new sites reuse it (it is
rebuilt only after a SpawnWP update that changes it, or with `SPAWNWP_REBUILD=1`).

## The WP-CLI console

Every SpawnWP site ships [WP-CLI](https://wp-cli.org/) inside its PHP container, ready
to use. The **⌨ WP-CLI** button on a site card opens a one-line console: type a command
(with or without the leading `wp`), press Enter, and the output streams live into the
card's output box — long jobs such as `wp media regenerate` or a big
`wp search-replace` show their progress line by line. Commands registered by installed
plugins work too (for example `wp wc ...` once WooCommerce is active). Press ↑/↓ to
recall earlier commands from the session.

Each run is a single, non-interactive `wp` process executed inside that site's PHP
container. That model has a few practical consequences:

- **No interactive commands.** `wp shell`, `wp db cli`, `--prompt` and anything that
  opens an editor need a real terminal and are rejected with an explanation. Passing
  the input as arguments works instead: `wp db query "SELECT ..."` is fine.
- **Confirmations need `--yes`.** Commands that normally ask "Are you sure?"
  (`wp db reset`, `wp site empty`, `wp plugin uninstall` ...) cannot prompt here: they
  abort safely without doing anything, and the console reminds you to add `--yes` to
  confirm.
- **No shell operators.** The command runs as one process, not through a shell, so
  `|`, `>`, `&&` and backticks have no effect. Use WP-CLI's own `--format=csv`,
  `--format=json` or `--field=...` options and copy the output from the box.
- **No file uploads from the browser.** A command that reads a file, such as
  `wp db import backup.sql`, only sees files already inside the container. The site's
  `wp-content` is a host bind mount (`/srv/<site>/projects/primary/wp-content/`), so
  placing a file there from the host makes it reachable at
  `/var/www/html/wp-content/`.

!!! note "Scope and safety"
    The console is not a sandbox — `wp eval` runs arbitrary PHP, and that is by
    design: it is your site. Everything executes as the web user inside that one
    site's container, never on the host, and a cockpit session is required. Commands
    only affect the site whose card you opened, and a disposable site is always one
    snapshot (or one re-spawn) away from a clean state.

## The file browser

The **📂 Files** button on a site card opens a file browser for that site, rooted at the
WordPress document root (`/var/www/html`). Use it to read and edit the files that live
outside WordPress's own media library and editor — `wp-config.php`, `.htaccess`,
mu-plugins, drop-ins, a plugin's `.env`, or a debug log — without opening an SSH session.

- **Browse.** Click a folder to open it; the breadcrumb walks back up. Directories are
  listed first, then files, each with its size and modification time.
- **View & edit.** Click a text file to open it in an inline editor. Save writes it back
  in place. Files larger than 1 MiB, and binary files, are offered as a download instead.
- **Download.** The **⬇** action streams any file out as-is, at any size.
- **Upload, New folder, Rename, Delete.** Drop a file into the current folder, create
  subfolders, move/rename, or remove files and folders.
- **Upload folder.** **⬆ Upload folder** takes a whole directory tree — a theme, a plugin,
  a build output — and recreates its structure on the site. Files go up one at a time, with
  a running count, so a large tree takes a while but never floods the server.
- **Extract a zip.** Any `.zip` in the listing gains a **📦** button that unpacks it into the
  folder it sits in, overwriting files of the same name.

!!! warning "Archives are checked before they are unpacked"
    A zip can contain entries like `../../wp-config.php` that, extracted naively, write
    outside the folder you chose — a "zip slip". SpawnWP reads the archive's index first and
    refuses the **whole** archive if any entry is an absolute path or contains `..`, naming
    the offending entry. Archives that would expand past 2 GiB, or hold more than 20,000
    entries, are refused as well, so a bad upload cannot fill the disk.

Everything runs **inside that one site's PHP container**, as the web user (`www-data`), so
uploaded and edited files get the ownership WordPress needs and nothing can reach the host
or another site — the container boundary is the jail. Browsing, viewing and downloading
are always available; the write actions (**save, upload, delete, rename, new folder**) are
sensitive and prompt for a recent Passkey confirmation the same way Destroy and Restore do.

!!! note "Scope and safety"
    The browser is not a sandbox: editing a file in the docroot changes your live site,
    and writing PHP into a folder the server executes runs that code — by design, it is
    your site. It only ever touches the site whose card you opened, never the host, and a
    disposable site is always one snapshot (or one re-spawn) away from a clean state. Take
    a **💾 Snapshot** before a risky edit.

## The System tab

**System** shows the host resources plus everything about the shared PHP images —
the real fixed cost on disk (~1.8&nbsp;GB per PHP version in use):

- **PHP images**: size, age and which sites use each image. Keeping an image means
  every deploy on that PHP version takes ~35 seconds; **Delete** (available only for
  images no site uses, with a typed confirmation) frees the space, at the price of a
  ~5-minute rebuild on the next deploy of that version. **Refresh** rebuilds an image
  now with the latest WordPress — do it from time to time: deploys never rebuild
  automatically, and images older than 7 days are flagged as *stale* here and with a
  notice during deploys.
- **Auto-delete (optional)**: "auto-delete unused images after N days" — 0 (the
  default) means manual deletion only. Images used by at least one site are never
  auto-deleted, whatever their age. The check runs daily.
- **Docker disk usage**: images / containers / volumes / build-cache breakdown and the
  host filesystem headroom.

### Snapshots & restore

**Snapshot** saves a gzipped database dump plus a tar of `wp-content/uploads`. The last
10 database snapshots are kept automatically. **Restore** lists them (with size and a 📦
marker when uploads are included) and, after a confirmation, overwrites the site with the
chosen snapshot.

Snapshots can be **named**: double-click one in the list, or use the ✏️ button, and give it
something you will recognise in a week — "before the theme swap", "clean install". Clear
the name by saving an empty one. The **🗑** button deletes a snapshot, removing its database
dump, its uploads tarball and its name; because that destroys a restore point, it asks for
the same Passkey confirmation as a restore.

The files on disk keep their timestamp names (`20260714-093712.sql.gz`) and the labels live
in a small `backups/labels.json` beside them. That is deliberate: the timestamp is what the
restore path validates to make sure it only ever reads a real snapshot, and renaming files
to whatever was typed into a text box would have put that check at the mercy of the name.
Snapshots taken before 0.5.22 simply show up unnamed until you name them.

### Destroy

Destroy is intentionally guarded: it's only enabled when the site is **Down**, and it
asks for a typed confirmation of the site name. It removes the containers and volumes,
the `/srv/<name>` directory, and the site's nginx blocks on both domains.

### WordPress credentials & magic login

**🔑 WP Admin** shows the site's WordPress administrator credentials, read from its `.env`,
with a button to copy the password on its own.

Underneath sits **🔑 Magic login**: one click opens that site's `/wp-admin/` already signed
in, with no username or password to copy. New sites get it automatically. On a site created
before 0.5.23 — or one where you turned it off — press **Enable magic login** once and the
button lights up; it works on any existing site, nothing needs recreating.

**Disable magic login** removes it again. That is not a flag in a database: it deletes the
must-use plugin from the site, so the feature stops existing there even for someone holding
a link.

!!! note "What a magic link actually is"
    Enabling the feature installs a small must-use plugin
    (`wp-content/mu-plugins/spawnwp-autologin.php`). Pressing the button asks the cockpit
    for a fresh link that is **single-use** and **expires after two minutes** — whichever
    comes first. The link is a genuine way into WordPress that skips the login form, so
    treat it like a password: don't paste it into a chat or a ticket, and if you think one
    leaked, just press the button again — minting a new link is free, and an old link is
    dead the moment it is used or two minutes pass.

    SpawnWP never stores the link's secret, only its SHA-256, so nothing on the server can
    be replayed to get in. Magic login is also **excluded from Deploy packages**: if you
    publish a site elsewhere, the destination does not inherit it.

    To spawn sites without it, set `SPAWNWP_ENABLE_AUTOLOGIN=0` in `/etc/spawnwp/config.env`.

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
