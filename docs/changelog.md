---
description: Review SpawnWP release history, product changes, fixes and compatibility updates.
---

# Changelog

## 0.5.0

- **WP-CLI console**: every site card in the cockpit gains a **⌨ WP-CLI** button that
  opens a one-line console. Commands run as a single non-interactive `wp` process
  inside that site's PHP container (no shell, no host access) and the output streams
  live into the card's output box — long jobs show progress line by line. Session
  command history with ↑/↓.
- Interactive subcommands that need a real terminal (`wp shell`, `wp db cli`,
  `--prompt`) are rejected with an explanation; `wp db query "SELECT ..."` and the
  rest of WP-CLI — including commands registered by installed plugins — work as in
  a script. Commands that ask for confirmation abort safely without a `--yes`; the
  console reminds you to add it.
- Documented the console's model and limits in
  [Using the cockpit](using-the-cockpit.md#the-wp-cli-console).
- Telemetry (consented installs only): new aggregate counter `wp_cli_commands`.

## 0.4.0

- **Content blueprints**: capture an already-configured WordPress site as a reusable
  blueprint on your own SpawnWP server. Install the SpawnWP Deploy plugin (0.2.0+) on
  the configured site, pair it with a single-use code generated on the cockpit's
  **System → Template connections**, choose what to capture (plugin files, theme
  files, media uploads, database — all on by default) and press *Create blueprint*.
  The capture is pushed over Ed25519-signed chunked uploads (same request format as
  site-to-site deploys), verified, hardened and installed atomically; it then appears
  on the **Deploy** page with a `Template` badge, payload size, capture summary and
  an estimated spawn time.
- New blueprint **manifest schema v2** (`schema_version: 2`) for captured payloads,
  allowed only in `/etc/spawnwp/blueprints.d/`; payloads live under
  `/var/lib/spawnwp/blueprints/<id>/`. Existing schema v1 blueprints are unchanged.
- **Privacy by construction**: the capture rewrites the source site URL to a fixed
  placeholder before upload, so the source URL never reaches the SpawnWP server; users
  and passwords are never included; each spawned site gets fresh credentials and the
  placeholder is rewritten to the new site URL.
- **License caveat surfaced**: plugins that are not from WordPress.org are listed in
  the manifest and flagged both at capture time and on the Deploy card — sites spawned
  from the blueprint may require new license keys or re-activation for those plugins.
- Re-capturing with an existing blueprint id offers a **replace** flow: the old
  payload is kept until the new one is fully verified, then swapped atomically.
- The capture form lets you widen the allowed PHP versions; the default pins to the
  source site's PHP version.
- New cockpit ingest API under `/api/ingest/` (signature-authenticated, rate-limited)
  plus session-side endpoints to generate pairing codes, revoke connections and delete
  content blueprints. Existing installations get the nginx changes through an update
  migration.
- Telemetry (notice v3 consents only): new aggregate counter `blueprint_captures`.
- SpawnWP Deploy plugin **0.2.0-dev**: new "Create a SpawnWP blueprint from this
  site" panel, plugin inventory (wp.org vs premium/custom), capture options and
  database-content disclaimer. The existing one-time site transfer is unchanged.

## 0.3.17

- The installer now offers to **pre-build the shared PHP 8.3 image** (default: yes),
  trading ~5 extra minutes of installation and ~1.8 GB of disk for a fast (~35 s)
  first site creation. Skipping keeps today's behaviour: the first create builds the
  image. Scripted installs can pre-seed `PREBUILD_PHP_IMAGE=0/1`. A pre-build
  failure never fails the installation.

## 0.3.16

- **Telemetry notice v3**: installations that consent under the new notice also share
  aggregate performance counters (warm/cold create durations, failure counts,
  healthcheck timeouts), aggregate feature-usage counters (blueprints, temporary
  sites, PHP settings, image refresh/delete, PHP switches) and rounded machine
  specifications (CPU count, RAM, disk and Docker space). Existing v2 consents keep
  sending exactly the minimal payload they agreed to until their natural 90-day
  renewal. Still strictly anonymous and aggregate — never domains, IPs, email,
  usernames, site names, content or logs.
- Local aggregate counters are now collected in `/var/lib/spawnwp/metrics.json`
  regardless of telemetry consent (new `lib-metrics.sh` helper, atomic and
  best-effort) — groundwork for future cockpit statistics.
- The Deploy PHP settings badge now reads **"N custom values"** instead of the
  ambiguous "modified".

## 0.3.15

- Redesigned the **Deploy** page: a clear stepped flow (blueprint → configure →
  create), aligned fields, and a launch bar showing the destination URL, a live
  **expected-time estimate** (it knows whether the chosen PHP version's image is
  already built) and the Create button. The PHP settings section is now a proper
  collapsible panel with per-field hints, a "modified" indicator and a reset.
- Blueprint cards now show the **approximate deploy time** (~35 sec; ~1–2 min for
  Development, which installs plugins), with a note that the first deploy on a
  not-yet-built PHP version adds a one-off ~5-minute image build.
- **Temporary sites**: an optional lifetime at creation (1/3/7/30 days). Expired
  sites are destroyed automatically by an hourly check (no backups kept — they are
  disposable by design). Manage shows a countdown badge and lets you extend the
  lifetime or make the site permanent.
- Fixed the System tab stuck on "Loading": the asset cache-busting version was not
  bumped in 0.3.14, so browsers kept the previous JavaScript. The version string is
  now tied to the SpawnWP release and enforced by a static test.
- Fixed `destroy-project.sh` leaving orphaned nginx location blocks behind on hosts
  serving from `sites-enabled/default` — dangerous once internal ports get reused
  by new sites.

## 0.3.14

- New **System** cockpit tab: inventory of the shared PHP images (size, age, which
  sites use them) with manual **Refresh** (rebuild now with the latest WordPress) and
  **Delete** (only for images no site uses), plus a Docker disk-usage breakdown.
- Removed the automatic 7-day image rebuild introduced in 0.3.13: deploys never pay a
  surprise 5-minute build any more. Images older than 7 days are flagged as stale (in
  System and with a deploy notice) and refreshed only when you choose.
- Optional **auto-delete of unused images** after a configurable number of days
  (0 = manual only, the default); images used by any site are never touched. A daily
  timer is installed by a migration on existing hosts.
- Per-site **PHP settings**: memory_limit, upload/post sizes, execution time,
  input vars/time and display_errors, settable in the Deploy form (advanced section)
  and editable later from Manage (~2s php restart, no image rebuild). Raising the
  upload sizes aligns the nginx limits automatically, up to 512M. Available for sites
  created from 0.3.14 onward.

## 0.3.13

- Site creation no longer rebuilds the PHP image every time: the image is built only
  on first use of a PHP version, when the build context changes, or when it is older
  than 7 days (`SPAWNWP_IMAGE_MAX_AGE_DAYS`; `SPAWNWP_REBUILD=1` forces a build).
  Creating a site on an already-built PHP version now takes about 35 seconds; the
  one-off first build per PHP version takes about 5 minutes, and the cockpit now
  shows a clear notice when a deploy includes it.
- Docker build cache is trimmed right after each image build, and the weekly prune
  now drops cache unused for 72 hours (was 168) — this cache could previously grow
  by several GB per created site.
- Fixed the MariaDB tuning config, which was never applied (the stack mounted a file
  that did not exist, so Docker created an empty directory in its place). New sites
  now use a small development profile: ~60 MB less preallocated disk and a lower
  memory footprint per database. Existing sites keep their current behaviour;
  recreate a site to pick up the new profile.

- Widened the first-run authenticator window to ±60 seconds so a small server clock
  drift no longer rejects every TOTP code during enrollment.
- Applied the same tolerance to password-plus-TOTP fallback sign-in; the single-use
  replay guard is unchanged.
- Replaced the generic setup rejection message with guidance pointing at the real
  causes: server clock skew and authenticator apps that ignore the SHA-256 algorithm.

## 0.3.3

- Removed the obsolete source-IP network gate from the installer and cockpit runtime.
- Added an upgrade migration that removes its services, package, configuration and
  installation metadata from existing hosts.

## 0.3.11

- Replaced raw PHP-switch build output in the cockpit with structured phases, a
  progress bar and an explicit first-download notice.
- Kept technical BuildKit output behind an expandable details control.
- Added `.env` rollback and recovery of the previous PHP service when a switch fails.

## 0.3.10

- Added inline Passkey reauthentication for sensitive cockpit actions after the
  ten-minute recent-login window expires.
- Sensitive actions now resume automatically after identity confirmation, while
  logout plus password/TOTP remains available as a fallback.
- Documented a mandatory clean-VPS release acceptance gate.

## 0.3.9

- Moved Docker Buildx state out of read-only `/root` when deployments are started
  by the sandboxed cockpit service.
- Added authenticated, one-click installation of signed SpawnWP updates from the
  Updates page, with restart-aware progress reporting.

## 0.3.8

- Removed the stale `cockpit-allowed.conf` include from per-environment Adminer and
  Mailpit routes generated during deployment.

## 0.3.7

- Fixed first-environment creation by copying the packaged `env.example` template
  to the new project's `.env.example` path.
- Added explicit site-name guidance and inline validation for spaces and other URL-
  unsafe characters on the Deploy page.

## 0.3.6

- Fresh installations now start with an empty cockpit instead of automatically
  building a `wp-dev` WordPress environment with the Development blueprint.
- The installer now reaches the activation report immediately after control-plane
  setup and explicitly introduces the one-time cockpit authentication flow.

## 0.3.5

- Replaced the installer domain regular expression with label-by-label validation
  and normalized accidental terminal whitespace before validation.

## 0.3.4

- Restored the missing runtime Nginx default server config so new environments can
  start cleanly.
- Accepted Ubuntu 26.04 in the installer compatibility check.
- Installer reruns now reset any previous SpawnWP footprint before provisioning,
  so partial attempts no longer leak state into the next bootstrap.
- The installer restores missing Let’s Encrypt Nginx support files when certbot
  leaves them absent on a reused host.
- Aligned the public requirements, README and website copy with the updated support
  matrix.

## 0.3.2

- Added the refreshed public website with an accessible cockpit screenshot slider.
- Published SpawnWP Deploy `0.1.0-dev` as an optional public preview with signed
  downloads and a focused user guide.
- Clarified SpawnWP's self-hosted lab positioning and managed-hosting boundary.

## 0.3.1

- Added explicit 90-day telemetry enable/disable controls to the Updates page.
- Added the minimal self-hosted telemetry receiver, retention cleanup and local report.
- Expanded the privacy notice with payload, retention and revocation details.

## 0.3.0

- Removed HTTP Basic Auth while retaining mandatory application authentication.
- Made SpawnWP passkey or password + TOTP authentication the sole cockpit login.
- Clarified first enrollment with explicit steps, authenticator examples and copyable
  TOTP/recovery material.
- Replaced ambiguous fallback-password terminology and expanded the installer's
  first-time activation instructions.
- Added Nginx rate limiting to authentication ceremonies.
- Added an idempotent host migration that validates Nginx before removing Basic Auth state.

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.2] — 2026-06-27

### Fixed

- TOTP enrollment QR code is now visible against the dark login background.

## [0.2.1] — 2026-06-27

### Fixed

- Cockpit static assets can be resolved in isolated validation environments while the
  production default remains `/srv/wp-cockpit/static`.

## [0.2.0] — 2026-06-27

### Added

- One-command Ubuntu/Debian installer with signed releases, TLS, primary environment
  provisioning and a root-only credentials report.
- Mandatory passkey login with password + TOTP fallback, recovery codes, server-side
  sessions, CSRF protection and root recovery.
- Separate, optional 90-day telemetry consent.

## [0.1.1] — 2026-06-27

### Fixed

- Update and rollback health checks now wait for the cockpit HTTP endpoint instead of
  treating an early systemd `active` state as application readiness.

## [0.1.0] — 2026-06-27

First public release.

### Added

- Signed GitHub Release updater with explicit update checks, transactional activation and
  rollback; existing WordPress environments remain untouched.
- Initial public documentation (MkDocs + Material).
- Web cockpit: spawn, start/stop/restart, snapshot/restore, destroy sites; live
  metrics; PHP-version switching; one-click Adminer and Mailpit.
- Two-domain architecture: WordPress content on `DOMAIN`, authenticated cockpit and
  admin tools on `COCKPIT_DOMAIN`, on a single SAN TLS certificate.
- Built-in WordPress.org QA toolchain: Plugin Check, Theme Check, PHP_CodeSniffer
  (WPCS) + PHPCompatibilityWP, PHPStan + WP stubs, Query Monitor, WP Crontrol, User
  Switching; per-site Mailpit.
- Security defaults: automatic HTTPS, dropped Linux capabilities, no Docker socket
  exposure, loopback-only service
  ports, per-install random secrets.

[Unreleased]: https://github.com/tts-empire/spawnwp/compare/v0.3.11...HEAD
[0.3.11]: https://github.com/tts-empire/spawnwp/compare/v0.3.10...v0.3.11
[0.3.10]: https://github.com/tts-empire/spawnwp/compare/v0.3.9...v0.3.10
[0.3.9]: https://github.com/tts-empire/spawnwp/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/tts-empire/spawnwp/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/tts-empire/spawnwp/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/tts-empire/spawnwp/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/tts-empire/spawnwp/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/tts-empire/spawnwp/compare/v0.3.3...v0.3.4
[0.2.2]: https://github.com/tts-empire/spawnwp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/tts-empire/spawnwp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tts-empire/spawnwp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/tts-empire/spawnwp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/tts-empire/spawnwp/releases/tag/v0.1.0
