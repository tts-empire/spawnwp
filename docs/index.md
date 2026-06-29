# SpawnWP

**A self-hosted WordPress lab for disposable dev environments.**

SpawnWP turns a fresh Debian or Ubuntu server — cloud VM/VPS, dedicated or bare
metal — into a WordPress lab for temporary, isolated and
sacrificable development environments. A single installer sets up Docker, an
nginx TLS edge, and a web **cockpit** from which you spawn, reset, snapshot and
destroy WordPress environments — each in its own container stack.

When work is finished, you choose how to back it up, export it or publish it. The
[optional SpawnWP Deploy WordPress plugin](deploying-a-site.md) is one narrowly scoped
way to transfer a site once to a separate, fresh WordPress installation; SpawnWP does
not require it.

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
    The cockpit lives on its own subdomain behind **HTTPS and mandatory application
    authentication**. Passkeys are preferred; password + TOTP is the alternative.

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

SpawnWP uses **two hostnames** that you choose and point at your server:

| Hostname | Serves |
|---|---|
| `DOMAIN` (e.g. `dev.example.com`) | Every spawned WordPress site (`/<site>/`) — content only |
| `COCKPIT_DOMAIN` (e.g. `cockpit.example.com`) | The authenticated cockpit dashboard and each site's Adminer / Mailpit |

Keeping admin tooling on its own subdomain means there is no conflict between
WordPress URLs and the cockpit. Every web interface uses ports 80/443.

## Get started

1. [Requirements](requirements.md) — a fresh server and two hostnames.
2. [DNS setup](dns-setup.md) — point your two hostnames at the server.
3. [Installation](installation.md) — run the one-liner.
4. [Accessing the cockpit](accessing-the-cockpit.md) — enroll, log in, create a site.

```bash
curl -fsSL https://spawnwp.com/install.sh | sudo bash
```

The installer asks for the two hostnames and your Let's Encrypt email, then starts an
empty cockpit and prints its one-time activation procedure.

!!! note
    SpawnWP provides a self-hosted lab for development, testing and demos. You control
    the server and choose how finished sites are backed up, exported or published. See
    [Security](security.md) for the threat model and limits.

## License

[MIT](https://github.com/OWNER/spawnwp/blob/main/LICENSE). Free to use, modify and
share.
