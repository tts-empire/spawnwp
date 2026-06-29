---
description: Review SpawnWP release history, product changes, fixes and compatibility updates.
---

# Changelog

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
