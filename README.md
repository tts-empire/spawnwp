<!-- markdownlint-disable MD033 MD041 -->
<h1 align="center">SpawnWP</h1>

<p align="center">
  <strong>A free, open-source, self-hosted WordPress sandbox and remote development lab.</strong><br>
  Bring your server. Spawn temporary WordPress projects without server babysitting.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-blue.svg"></a>
  <img alt="Docs: MkDocs Material" src="https://img.shields.io/badge/docs-MkDocs%20Material-526CFE">
  <img alt="Platform: Ubuntu/Debian" src="https://img.shields.io/badge/platform-Ubuntu%20%7C%20Debian-E95420">
  <img alt="Arch: amd64/arm64" src="https://img.shields.io/badge/arch-amd64%20%7C%20arm64-444">
</p>

---

SpawnWP turns a fresh Debian or Ubuntu server — cloud VM/VPS, dedicated or bare
metal — into a WordPress lab for temporary, isolated and
sacrificable development environments. A single installer sets up Docker, an
nginx TLS edge, and a web **cockpit** from which you spawn, reset, snapshot and
destroy WordPress environments — each in its own container stack.

→ [Self-hosted WordPress sandbox](https://spawnwp.com/wordpress-sandbox/) ·
[Use cases](https://spawnwp.com/use-cases/) ·
[Guides](https://spawnwp.com/guides/) ·
[Alternatives](https://spawnwp.com/alternatives/)

The goal is brutally simple:

```text
install -> open cockpit -> create site -> done
```

No hand-built nginx config, no Docker commands to remember, no shared test site
to accidentally break. Build a plugin, test a theme, demo a project, destroy the
environment and create another one. Every site ships ready for **WordPress.org
plugin/theme development** through the default Development blueprint: Plugin Check, Theme Check, PHP_CodeSniffer (WPCS),
PHPStan, Query Monitor, Mailpit and more, preinstalled.

The cockpit lives on its own subdomain and is protected by HTTPS plus mandatory
application authentication: passkey preferred, or password with TOTP and recovery codes.

## Highlights

- **One-command install** — `curl … | bash`, no manual setup.
- **Web cockpit first** — create environments, start/stop, snapshot, restore and
  destroy without memorizing commands.
- **Two-domain design** — your content domain stays pure WordPress; all admin tooling
  lives on a separate, application-authenticated cockpit subdomain.
- **WordPress.org QA built in** — the exact checks the .org review runs, in-browser
  and on the CLI.
- **Secure by default** — random per-install secrets, dropped Linux capabilities,
  no Docker socket exposure, loopback-only service ports, automatic TLS.
- **Portable** — Ubuntu 22.04/24.04/26.04 and Debian 12/13, amd64 and arm64; web traffic uses
  ports 80/443.
- **A lab, not a hosting panel** — use it for development, testing and demos on
  infrastructure you control, not for production hosting or client accounts.

## Quickstart

You need a fresh supported server (root) and **two hostnames you control**, both
pointing at it — one for your sites, one for the cockpit. The installer handles the server
setup; after that, day-to-day work happens in the browser.

```bash
curl -fsSL https://spawnwp.com/install.sh | sudo bash
```

The installer asks for your content hostname, cockpit hostname and Let's Encrypt email.
For automated installs you can pass them up front:

```bash
curl -fsSL https://spawnwp.com/install.sh \
  | sudo DOMAIN=dev.example.com COCKPIT_DOMAIN=cockpit.example.com EMAIL=you@example.com bash
```

When it finishes, the installer prints (and saves to `/root/spawnwp-credentials.txt`)
your URLs and the one-time cockpit activation procedure. It does not create a
WordPress environment automatically.

Then the workflow is:

1. Open the cockpit URL from the report.
2. Complete administrator activation.
3. Click **Create site**.
4. Use the new WordPress site.

→ **Full documentation:** <https://spawnwp.com/docs/>

## Documentation

| Guide | |
|---|---|
| [Requirements](docs/requirements.md) | What you need before installing |
| [DNS setup](docs/dns-setup.md) | Point your two hostnames at the server |
| [Installation](docs/installation.md) | The one-liner, explained |
| [Accessing the cockpit](docs/accessing-the-cockpit.md) | Passkeys, TOTP and recovery access |
| [Using the cockpit](docs/using-the-cockpit.md) | Create, reset and destroy environments |
| [WordPress development](docs/wordpress-development.md) | Plugins, themes, QA tools |
| [Architecture](docs/architecture.md) · [Security](docs/security.md) | How it works & threat model |

## License

[MIT](LICENSE) — © 2026 spawnwp contributors.

## Roadmap

SpawnWP is evolving as a self-hosted WordPress lab for teams. Planned directions
include:

- **Team access** — invited users, shared site visibility, and clear admin/member
  permissions.
- **Disposable-site lifecycle** — configurable expiry, advance warnings, and a safe
  grace period before automatic cleanup.
- **Reusable environments** — fast local cloning and versioned full-site templates
  containing the database, uploads, plugins, and themes.
- **Developer automation** — API tokens, CLI workflows, and Git integration for
  repeatable plugin and theme development.
- **Safer sharing and compatibility testing** — temporary demo access, one-click
  WordPress admin links, and optional multisite environments.

These are planned product directions, not delivery commitments or guaranteed dates.
SpawnWP will remain focused on controlled, disposable development environments rather
than anonymous public provisioning, WaaS, or production staging synchronization.
