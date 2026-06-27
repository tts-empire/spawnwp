# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- One-command installer for Ubuntu/Debian (amd64/arm64).
- Web cockpit: spawn, start/stop/restart, snapshot/restore, destroy sites; live
  metrics; PHP-version switching; one-click Adminer and Mailpit.
- Two-domain architecture: WordPress content on `DOMAIN`, knock-protected cockpit and
  admin tools on `COCKPIT_DOMAIN`, on a single SAN TLS certificate.
- Built-in WordPress.org QA toolchain: Plugin Check, Theme Check, PHP_CodeSniffer
  (WPCS) + PHPCompatibilityWP, PHPStan + WP stubs, Query Monitor, WP Crontrol, User
  Switching; per-site Mailpit.
- Security defaults: port-knocking with sliding sessions, HTTP Basic Auth, automatic
  HTTPS, dropped Linux capabilities, no Docker socket exposure, loopback-only service
  ports, per-install random secrets.

[Unreleased]: https://github.com/tts-empire/spawnwp/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/tts-empire/spawnwp/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/tts-empire/spawnwp/releases/tag/v0.1.0
