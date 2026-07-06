---
description: Import an existing WordPress site into a SpawnWP environment — capture your live or local site as a reusable content blueprint and spawn fresh SpawnWP environments from it. A goal-oriented walkthrough of the SpawnWP Deploy capture flow (not a live migration or staging sync).
---

# Import an existing WordPress site

You can bring a WordPress site you already run — live, staging or local — into your
SpawnWP lab and spawn fresh environments from it. This page is the step-by-step
walkthrough; the mechanics it relies on are documented under
[Content blueprints](blueprints.md#content-blueprints-captured-from-a-site).

!!! warning "This captures a reusable blueprint — it is not a live migration"
    Importing here means **capturing your site as a reusable content blueprint** and then
    spawning new SpawnWP environments from it. It is **not** a live migration, a mirror or a
    staging sync, and it does not point SpawnWP at your production site.

    - Your existing site is never modified and never taken over.
    - **User accounts and passwords are never captured** (the `users` and `usermeta` tables
      are excluded). Every spawned environment gets its own **fresh administrator**.
    - The captured content — posts, pages, settings, uploads, theme and plugin files — is
      baked into the blueprint, so it appears in **every** site you spawn from it.

    See [Keep these actions separate](deploying-a-site.md#keep-these-actions-separate) if you
    instead want to *publish* a finished SpawnWP site out to another host.

## What you'll end up with

A **content blueprint** listed under *Your blueprints* on the cockpit's **Deploy** page.
From then on it behaves like any built-in blueprint: pick it, choose a PHP version, and
spawn as many disposable, isolated environments (nginx, PHP, MariaDB, Mailpit, Adminer) as
you like — each a fresh copy of your captured site.

## Before you start

On the **source site** (the WordPress you are importing):

- WordPress **single-site** (Multisite is not supported).
- **PHP 7.4+** with the `sodium` and ZIP extensions.
- Trusted HTTPS and a reachable WordPress REST API.
- Directly writable plugins, themes and uploads.
- A capture payload of **up to 2 GiB**. If uploads push you over, exclude them and
  re-capture (see [When to choose another method](#when-to-choose-another-method)).

The PHP major/minor of the environments you spawn should match the source; the capture
defaults the allowed PHP versions to the source site's version.

## Step 1 — Get the SpawnWP Deploy plugin onto the source site

The importer is the optional **SpawnWP Deploy** WordPress plugin.

- **If the source site was created inside SpawnWP**, the quickest way is to tick
  *Install the SpawnWP Deploy plugin* when you create it — it arrives installed and
  activated from the signed copy bundled with SpawnWP.
- **On your own external WordPress**, install it by hand: download the signed package and
  verify its checksum and Ed25519 signature first. Follow
  [Install the WordPress plugin](deploying-a-site.md#install-the-wordpress-plugin).

## Step 2 — Generate a pairing code in the cockpit

In the cockpit, open the **System** page and, under **Blueprint capture**, generate a
**pairing code**. It is **single-use** and valid for **15 minutes**.

## Step 3 — Capture the site as a blueprint

On the source site, open the plugin's *Create a SpawnWP blueprint from this site* panel:

1. Paste the pairing code.
2. Choose what to capture — **plugin files, theme files, media uploads and the database**
   are all enabled by default. The plugin asks you to confirm the database capture, because
   your real posts, pages and settings will appear in every spawned site.
3. Press **Create blueprint**. The payload is pushed to your server in **signed,
   checksummed chunks** (up to 2 GiB). The upload is atomic: an interrupted capture never
   leaves a half-installed blueprint.

The blueprint then appears under **Your blueprints** on the **Deploy** page, with its size
and a capture summary.

## Step 4 — Spawn an environment from it

On the **Deploy** page, pick your new blueprint, choose a PHP version, and create the site.
Spawn as many as you need — the blueprint is reusable.

## After importing: what's different

Two things are specific to sites spawned from a captured blueprint (details under
[Spawning from a captured blueprint](blueprints.md#spawning-from-a-captured-blueprint)):

- **A fresh administrator is created.** Use the credentials shown under **🔑 WP
  credentials** on the site's **Manage** page — *not* your source site's login.
- **Plugins start deactivated by default.** A captured site can carry security or login
  plugins (IP allow-lists, passwordless login, lockouts) that would otherwise lock you out
  of the fresh site. They are installed but inactive; an admin notice reminds you to
  **reactivate them one at a time**. Uncheck *Start with all plugins deactivated* on the
  Deploy page to start with them active, as captured.

Also worth knowing:

- **The origin's WordPress version is reproduced.** The blueprint pins the source site's
  exact WordPress version (e.g. `6.5.2`), so spawned sites start on the same release
  rather than the latest. On the **Deploy** page you can keep it (default) or choose
  **Latest**. Pinning freezes core security updates — apply them inside the spawned site.
- The source site URL is rewritten to a fixed placeholder **before upload** and never
  reaches the server; every spawned site rewrites it to its own URL.
- Plugins that are **not from WordPress.org** are flagged at capture time and on the Deploy
  card — spawned sites may need new licence keys or re-activation for them.
- The payload stays on your server and is **not part of telemetry**. Delete a blueprint —
  manifest and payload — any time from **System → Content blueprints**; existing sites are
  unaffected.

## When to choose another method

This flow is the wrong tool when you need to:

- import into a destination that **already has content** or is **live**;
- perform **repeated pushes**, content merging or selective/continuous sync;
- move a **Multisite** network;
- transfer **more than 2 GiB**, or use object-storage uploads.

For those, use a dedicated migration or backup plugin instead. See
[What the plugin does not replace](deploying-a-site.md#what-the-plugin-does-not-replace).

## See also

- [Content blueprints](blueprints.md#content-blueprints-captured-from-a-site) — the
  reference for how capture and spawning work.
- [Publishing or exporting a finished site](deploying-a-site.md) — the reverse direction,
  and the plugin's install and verification steps.
- [Troubleshooting](troubleshooting.md).
