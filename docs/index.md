# spawnwp

**A self-hosted WordPress lab for disposable dev environments.**

spawnwp turns a fresh VPS into a WordPress lab for temporary, isolated and
sacrificable development environments. A single installer sets up Docker, an
nginx TLS edge, and a web **cockpit** from which you spawn, reset, snapshot and
destroy WordPress environments — each in its own container stack.

```text
install -> open cockpit -> create site -> done
```

You should not have to configure servers by hand, remember Compose commands, or
reuse one fragile test environment for everything. Build a plugin, test a theme,
demo a project, destroy the environment and create another one.

The default **Development** blueprint is ready for **WordPress.org plugin and theme development**, with
Plugin Check, Theme Check, PHP_CodeSniffer (WordPress Coding Standards), PHPStan,
Query Monitor and Mailpit preinstalled.

!!! tip "Protected cockpit"
    The cockpit lives on its own subdomain, behind **HTTP Basic Auth + HTTPS**.
    Strongly recommended port-knocking is enabled by default but can be turned off.
    Your content domain stays pure WordPress.

## What you get

- **A web cockpit** to create environments, start/stop, restart, snapshot,
  restore and destroy; switch PHP versions; watch live CPU/RAM; and open Adminer
  or Mailpit in one click.
- **Isolated sites** — each spawned site has its own nginx, PHP-FPM, MariaDB, Mailpit
  and Adminer containers, reachable at `https://DOMAIN/<site>/`.
- **A built-in QA toolchain** mirroring the official WordPress.org review — in the
  browser (Plugin Check / Theme Check) and on the CLI (phpcs / phpstan).
- **Secure defaults** — random per-install secrets, dropped Linux capabilities, no
  Docker socket in containers, loopback-only service ports, automatic Let's Encrypt TLS.

## How it's laid out

spawnwp uses **two hostnames** that you choose and point at your VPS:

| Hostname | Serves |
|---|---|
| `DOMAIN` (e.g. `dev.example.com`) | The primary WordPress site (`/`) and every spawned site (`/<site>/`) — content only |
| `COCKPIT_DOMAIN` (e.g. `cockpit.example.com`) | The cockpit dashboard and each site's Adminer / Mailpit; port-knocking is optional and enabled by default |

Keeping admin tooling on its own subdomain means there is no conflict between
WordPress URLs and the cockpit. Every web interface uses 80/443; when port-knocking is
enabled, its three generated TCP ports must additionally pass any provider-level firewall.

## Get started

1. [Requirements](requirements.md) — a fresh VPS and two hostnames.
2. [DNS setup](dns-setup.md) — point your two hostnames at the VPS.
3. [Installation](installation.md) — run the one-liner.
4. [Accessing the cockpit](accessing-the-cockpit.md) — knock, log in, create a site.

```bash
curl -fsSL https://spawnwp.com/install.sh | sudo bash
```

The installer asks for the two hostnames and your Let's Encrypt email, then prints the
cockpit URL and login details.

!!! note
    spawnwp is built for test environments, demos and development labs. It is not
    managed hosting. See [Security](security.md) for the threat model and limits.

## License

[MIT](https://github.com/OWNER/spawnwp/blob/main/LICENSE). Free to use, modify and
share.
