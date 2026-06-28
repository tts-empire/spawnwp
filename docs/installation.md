# Installation

With your [two hostnames pointing at the VPS](dns-setup.md), install SpawnWP with a
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
| `ENABLE_TELEMETRY` | no | `0` (default) or explicit 90-day opt-in with `1` |

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
   nginx, certbot and supporting tools.
2. Generates **fresh random secrets** for this install (databases, WordPress admin,
   WordPress and application authentication).
3. Deploys the stack to `/srv` and the cockpit app, builds the WordPress/PHP image.
4. Configures nginx for both hostnames and obtains a **single SAN TLS certificate**
   covering `DOMAIN` and `COCKPIT_DOMAIN`.
5. Creates the application-auth database, encryption key and one-time activation code.
6. Provisions the **primary WordPress site**, including the dev toolkit and QA plugins.
7. Prints a **credentials report**.

It typically takes a few minutes (longer on the first image build).

## What you do next

After the installer finishes, normal work moves to the browser:

1. Open the cockpit URL from the report.
2. Enter the one-time activation code, choose an administrator password, register a passkey and
   scan the TOTP QR code.
3. Store the ten single-use recovery codes shown once by the cockpit.
4. Click **Create site**.

You can still use the CLI when you want to, but it should not be required for the
daily create/test/reset loop.

## The credentials report

At the end, the installer prints and saves to `/root/spawnwp-credentials.txt`
(permissions `600`) everything you need:

```text
spawnwp — installation complete

Sites:    https://dev.example.com/
Cockpit:  https://cockpit.example.com/

COCKPIT FIRST-TIME ACTIVATION

1. Open: https://cockpit.example.com/
2. Enter this one-time activation code:

   ••••••••••••••••

   Valid for 24 hours and usable once. This is not your password.

3. Create the administrator username and password.
4. Scan the QR code with a TOTP authenticator app.
5. Create a passkey when prompted by the browser.
6. Save the ten recovery codes shown at the end.

WordPress admin (primary site)
  user: admin-xxxxxx
  pass: ••••••••••••••••

This root-only report is stored at:
  /root/spawnwp-credentials.txt

Read it again with:
  sudo cat /root/spawnwp-credentials.txt
```

!!! danger "Save these now"
    The report is root-readable with mode `600`; the activation code expires after 24
    hours and is invalidated after use. Store credentials and recovery codes in your
    password manager. Never commit or share the report or your `.env` files.

## Optional telemetry

The separate prompt `Share anonymous usage statistics for 90 days? [y/N]` defaults to
No. Consent expires automatically. Payloads contain a random installation ID, platform
versions, optional feature flags and aggregate counters. They exclude domains, IPs,
email, usernames, site names, content, plugins, logs and credentials.

```bash
spawnwp telemetry status
spawnwp telemetry payload
sudo spawnwp telemetry disable
sudo spawnwp telemetry enable
```

The same control is available on the cockpit Updates page. Enabling creates a fresh
random identifier and consent valid for 90 days. Revocation stops collection, requests
deletion of the receiver record, and deletes the local identifier and queue. Endpoint
failure never blocks installation or cockpit operation; inactive receiver records expire
after 90 days.

## Re-running / forcing

The installer refuses to run if SpawnWP is already installed, to avoid clobbering data.
To reinstall from scratch, pass `--force` (this is destructive):

```bash
… bash -s -- --force
```

## Next

→ [Accessing the cockpit](accessing-the-cockpit.md) — enroll and log in securely.
