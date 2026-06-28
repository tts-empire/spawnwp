# Changelog

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

[Unreleased]: https://github.com/tts-empire/spawnwp/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/tts-empire/spawnwp/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/tts-empire/spawnwp/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tts-empire/spawnwp/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/tts-empire/spawnwp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/tts-empire/spawnwp/releases/tag/v0.1.0
