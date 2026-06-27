# Installation

With your [two hostnames pointing at the VPS](dns-setup.md), install spawnwp with a
single command, run as **root**. The installer is meant to get you out of SSH and
into the cockpit as quickly as possible.

## The one-liner

```bash
curl -fsSL https://spawnwp.com/install.sh | sudo bash
```

The installer prompts for the values it needs:

| Variable | Required | Meaning |
|---|---|---|
| `DOMAIN` | yes | Hostname for your WordPress sites |
| `COCKPIT_DOMAIN` | yes | Hostname for the cockpit + admin tools |
| `EMAIL` | yes | Contact email for Let's Encrypt |
| `BASIC_AUTH_USER` | no | HTTP Basic Auth username (default: `admin`) |
| `ENABLE_PORT_KNOCKING` | no | `1` (default, strongly recommended) or `0` |

Interactive installs ask whether to enable port-knocking. Press **Enter** to accept the
recommended default (`Y`). If you disable it, the cockpit remains protected by HTTPS
and HTTP Basic Auth, but its login endpoint is publicly reachable and easier to scan or
brute-force.

For automated installs, pass the same values as environment variables:

```bash
curl -fsSL https://spawnwp.com/install.sh \
  | sudo DOMAIN=dev.example.com COCKPIT_DOMAIN=cockpit.example.com EMAIL=you@example.com bash
```

!!! note "Review before running"
    To review the script first, download it and run it yourself:
    ```bash
    curl -fsSL https://spawnwp.com/install.sh -o install.sh
    less install.sh
    sudo DOMAIN=… COCKPIT_DOMAIN=… EMAIL=… bash install.sh
    ```

## What the installer does

1. Detects the OS (Ubuntu/Debian) and installs prerequisites: Docker Engine + Compose,
   nginx, certbot, supporting tools and, when selected, knockd.
2. Generates **fresh random secrets** for this install (databases, WordPress admin,
   Basic Auth password if not provided, and, when enabled, the port-knock sequence).
3. Deploys the stack to `/srv` and the cockpit app, builds the WordPress/PHP image.
4. Configures nginx for both hostnames and obtains a **single SAN TLS certificate**
   covering `DOMAIN` and `COCKPIT_DOMAIN`.
5. When selected, sets up **port-knocking** (knockd) and its idle-session reaper. This
   protection is enabled and strongly recommended by default.
6. Provisions the **primary WordPress site**, including the dev toolkit and QA plugins.
7. Prints a **credentials report**.

It typically takes a few minutes (longer on the first image build).

## What you do next

After the installer finishes, normal work moves to the browser:

1. Open the cockpit URL from the credentials report.
2. Log in with the Basic Auth credentials.
3. Click **Create site**.
4. Use the new WordPress site; snapshot or destroy it when you are done.

You can still use the CLI when you want to, but it should not be required for the
daily create/test/reset loop.

## The credentials report

At the end, the installer prints and saves to `/root/spawnwp-credentials.txt`
(permissions `600`) everything you need:

```text
spawnwp — installation complete

Sites:    https://dev.example.com/
Cockpit:  https://cockpit.example.com/

HTTP Basic Auth
  user: admin
  pass: ••••••••••••••••

WordPress admin (primary site)
  user: admin-xxxxxx
  pass: ••••••••••••••••

Port-knock sequence (open):  <p1>, <p2>, <p3>
  Open the cockpit with:
    ./clients/knock.sh cockpit.example.com <p1> <p2> <p3>
```

When port-knocking is disabled, the report instead states `Port-knocking: disabled`,
prints a security warning, and gives the direct cockpit URL. No knock sequence or extra
TCP ports are required in that mode.

!!! danger "Save these now"
    The secrets are shown once and are not recoverable elsewhere (only stored in the
    `600`-mode file on the server). Copy them to your password manager. Never commit or
    share the report or your `.env` files.

## Re-running / forcing

The installer refuses to run if spawnwp is already installed, to avoid clobbering data.
To reinstall from scratch, pass `--force` (this is destructive):

```bash
… bash -s -- --force
```

## Next

→ [Accessing the cockpit](accessing-the-cockpit.md) — knock when enabled, then log in.
