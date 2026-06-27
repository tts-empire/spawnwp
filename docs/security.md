# Security

spawnwp is a **WordPress development lab** with sensible, layered defaults. This
page describes the protection model and, just as importantly, its limits.

## Access layers

| Surface | Protection |
|---|---|
| `COCKPIT_DOMAIN` (default/recommended) | **Port-knock** → **HTTP Basic Auth** → **HTTPS** |
| `COCKPIT_DOMAIN` (knocking disabled) | **HTTP Basic Auth** → **HTTPS** |
| `DOMAIN` (WordPress sites) | **HTTP Basic Auth** → **HTTPS** |

- **Port-knocking** keeps the admin surface dark: the cockpit returns `403` until your IP
  sends the secret sequence ([details](accessing-the-cockpit.md)). The sequence is random
  per install. Sessions slide for 30 minutes of inactivity, then a reaper revokes the IP.
  It is optional in the installer, strongly recommended, and enabled by default.
- **HTTP Basic Auth** guards both hostnames with a username and a random password.
- **HTTPS** everywhere via Let's Encrypt; HTTP is redirected to HTTPS.

## Container hardening

Every container runs with:

- `cap_drop: ALL`, adding back only the few capabilities each service needs,
- `security_opt: no-new-privileges`,
- **no** privileged mode,
- **no** Docker socket mounted (the cockpit shells out to `docker compose` on the host,
  never inside a container),
- resource limits (CPU/memory) per service.

All service ports (PHP-FPM, MariaDB, Mailpit, Adminer, Redis, the cockpit app) bind to
**127.0.0.1 only**. The host nginx is the single public web entry point on 80/443.
When enabled, `knockd` also observes the three generated TCP knock ports, where no
application service is listening.

## Secrets

- All secrets are **generated fresh per install**: database passwords, the WordPress
  admin password, the Basic Auth password (unless you supply a username), and the knock
  sequence.
- They are shown once in the install report and saved to `/root/spawnwp-credentials.txt`
  (mode `600`).
- Per-site secrets live in each site's `.env`, which is git-ignored and never leaves the
  server.
- The WordPress admin username is randomized (e.g. `admin-1a2b3c`), not the predictable
  `admin`.

## WordPress hardening

- `DISALLOW_FILE_EDIT` is enabled (no in-dashboard file editor).
- The default `akismet` and `hello` plugins are removed on every site.
- Xdebug is **off** by default.

## What spawnwp is — and is not

!!! warning "Intended use"
    spawnwp is built for **test environments, demos and development labs** run by a
    trusted operator (you). It is a way to create disposable WordPress environments
    on your own VPS without turning that VPS into a managed hosting platform.

!!! danger "Not a hardened multi-tenant production host"
    It is **not** designed to host untrusted users or production traffic at scale.
    Notably: all sites on a server share the host and the same Basic Auth realm; the
    cockpit operator has full control of every site; and WordPress/PHP run with broad
    in-container permissions suitable for development. If you put real production sites
    here, treat the whole VPS as a single trust boundary, keep it updated, and restrict
    who has the knock sequence and credentials.

## Reporting a vulnerability

Please report security issues privately to the maintainers rather than in a public
issue. Do not include real secrets, domains or knock sequences in any report.
