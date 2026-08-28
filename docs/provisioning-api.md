# Provisioning API

Create temporary WordPress sites from CI, a SaaS application, or an internal tool. The official
client handles pairing, Ed25519 signatures, nonces, and idempotency for you.

## Quickstart

### 1. Install the official client

The client requires Python 3.10 or newer and OpenSSL. It has no third-party Python dependencies.

```bash
install -d "$HOME/.local/bin"
curl -fsSL https://spawnwp.com/downloads/spawnwp-api \
  -o "$HOME/.local/bin/spawnwp-api"
chmod 755 "$HOME/.local/bin/spawnwp-api"
spawnwp-api --version
```

If `$HOME/.local/bin` is not in your `PATH`, invoke the client by its full path or add that
directory to `PATH`. The matching checksum is published at
`https://spawnwp.com/downloads/spawnwp-api.sha256`.

### 2. Pair the client

In the SpawnWP cockpit, open **System → Blueprint capture** and select
**Generate provisioning API pairing**. Copy the resulting `spawnbp1:` code within 15 minutes,
then run:

```bash
spawnwp-api pair 'spawnbp1:PASTE_THE_COMPLETE_CODE_HERE' \
  --label 'Demo integration'
```

The pairing code is single-use. The client generates a dedicated Ed25519 key locally; only its
public key is sent to SpawnWP.

Confirm the connection and current capacity:

```bash
spawnwp-api status
```

### 3. Create a site

```bash
spawnwp-api provision \
  --blueprint development \
  --expires 1h
```

Typical output:

```text
Project:      site-a1b2c3d4e5f6
URL:          https://wp.example.com/site-a1b2c3d4e5f6
Expires:      2026-07-30T16:00:00Z
Username:     site-a1b2c3d4e5f6-12ab34
Password:     generated-secret
Magic link:   https://wp.example.com/site-a1b2c3d4e5f6/?spawnwp_autologin=...
```

`magic_link` is included when auto-login is available. It is single-use and expires after two
minutes.

For automation, request JSON and select the fields you need:

```bash
site_json="$(spawnwp-api provision \
  --blueprint development \
  --expires 2h \
  --name pull-request-142 \
  --json)"

site_url="$(printf '%s' "$site_json" | jq -r '.url')"
printf 'Temporary site: %s\n' "$site_url"
```

Treat the JSON response as a secret: it contains the WordPress password and may contain a magic
login link.

## Client commands

| Command | Purpose |
| --- | --- |
| `spawnwp-api pair CODE` | Create and store a provisioning connection |
| `spawnwp-api status` | Show server version, quota, defaults, and active sites |
| `spawnwp-api provision` | Create one temporary WordPress site |
| `spawnwp-api magic-link PROJECT` | Mint two-minute access for an owned managed site |
| `spawnwp-api revoke --yes` | Revoke the server connection and remove local credentials |

### Provisioning options

| Option | Default | Description |
| --- | --- | --- |
| `--blueprint ID` | required | Blueprint to use, for example `development` |
| `--expires DURATION` | `1h` | `300`–`31536000` seconds, or a value such as `30m`, `2h`, `7d` |
| `--role ROLE` | `administrator` | Initial WordPress user role |
| `--group NAME` | `API` | Cockpit group for the site |
| `--name SLUG` | generated | Optional stable project name |
| `--access-profile PROFILE` | `standard` | Use `restricted-admin` for a guarded evaluation site |
| `--credentials-mode MODE` | `return` | Use `managed` to omit reusable credentials |
| `--idempotency-key KEY` | generated | Stable key for retrying the same logical request |
| `--timeout SECONDS` | `610` | Client-side HTTP timeout |
| `--json` | off | Machine-readable output |

Allowed roles are `administrator`, `editor`, `author`, `contributor`, and `subscriber`.
`administrator` is the default because evaluation environments commonly need plugin, theme, and
settings access. Choose a narrower role when the workflow does not need administration.

The client generates a new idempotency key for every `provision` command. If an automation job may
retry after losing the response, provide its own stable key:

```bash
spawnwp-api provision \
  --blueprint development \
  --idempotency-key 'order-4831-attempt-1' \
  --json
```

Repeating that command with the exact same arguments returns the stored result without creating a
second site. Reusing the key with a different request returns `409`.

## Credentials and lifecycle

By default, the client stores:

```text
~/.config/spawnwp/api.json
~/.config/spawnwp/api.key
```

Both files are created with mode `0600`; the containing directory is mode `0700`. Use a separate
connection per application or environment:

```bash
spawnwp-api --config "$HOME/.config/spawnwp/ci.json" pair 'spawnbp1:...'
spawnwp-api --config "$HOME/.config/spawnwp/ci.json" status
```

Back up neither file unless your secret-management policy requires it. To rotate credentials,
revoke the old connection and pair again with a new code.

```bash
spawnwp-api revoke --yes
```

Revocation immediately prevents new API calls and removes the local key and configuration.
Existing temporary sites are **not** destroyed; they continue until their normal expiry. An
administrator can also revoke any connection from the SpawnWP cockpit.

## HTTP API

The machine-readable [OpenAPI 3.1 specification](assets/provisioning-api-openapi.yaml) describes
the request and response schemas. The base URL is the HTTPS cockpit origin.

| Method and path | Purpose | Idempotency key |
| --- | --- | --- |
| `GET /api/provision/status` | Connection, defaults, quota, and active sites | no |
| `POST /api/provision` | Create a temporary site | required |
| `POST /api/provision/sites/{project}/magic-link` | Mint access to an owned managed site | no |
| `DELETE /api/provision/connection` | Revoke the calling connection | no |

### Create request

Only `blueprint` is required:

```json
{
  "blueprint": "development",
  "expires_seconds": 3600,
  "role": "administrator",
  "group": "API",
  "name": "customer-demo",
  "access_profile": "standard",
  "credentials_mode": "return"
}
```

Request fields are validated before any site is reserved:

| Field | Required | Constraint and behavior |
| --- | --- | --- |
| `blueprint` | yes | Lowercase slug matching `^[a-z0-9][a-z0-9-]{0,30}$` |
| `expires_seconds` | no | Integer from `300` to `31536000`; defaults to `3600` |
| `role` | no | `administrator`, `editor`, `author`, `contributor`, or `subscriber` |
| `group` | no | 1–32 letters, digits, spaces, dots, underscores, or hyphens; defaults to `API` |
| `name` | no | Project slug matching `^[a-z0-9][a-z0-9-]{0,30}$` |
| `access_profile` | no | `standard` or `restricted-admin`; defaults to `standard` |
| `credentials_mode` | no | `return` or `managed`; defaults to `return` |

`name` is the project identifier used in the URL, not a display name. Omit it to receive a
random name. Supplying an existing project name returns `409`. Optional fields must be omitted
when they have no value: an empty string is still a value and fails validation for fields such
as `group` and `name`.

The response contains credentials once provisioning finishes:

```json
{
  "project": "customer-demo",
  "url": "https://wp.example.com/customer-demo",
  "expires_at": 1785402000,
  "username": "customer-demo-12ab34",
  "password": "generated-secret",
  "magic_link": "https://wp.example.com/customer-demo/?spawnwp_autologin=..."
}
```

Provisioning is synchronous. The application timeout is 300 seconds and the reverse proxy allows
600 seconds for the application to return its result and cleanup status.

### Managed demo access

Public-facing integrations should request both `"access_profile":"restricted-admin"` and
`"credentials_mode":"managed"`. The first installs a SpawnWP-owned must-use plugin that blocks
plugin/theme installation, activation, deletion and editing, core updates, user management,
import/export and theme switching. It intentionally leaves ordinary WordPress administration and
product-specific capabilities available so a visitor can evaluate the selected theme or plugin.

Managed mode returns the username for ownership bookkeeping but omits the password and initial
magic link. It ensures the core auto-login MU plugin is present even when new sites normally have
that optional capability disabled. When the visitor explicitly asks to enter wp-admin, sign
`POST /api/provision/sites/{project}/magic-link`. The endpoint mints a single-use two-minute link
only when the project is active and belongs to the calling provisioning connection. Treat the URL
as a secret and redirect the intended browser immediately; never store it in a campaign database
or log it.

### Status response

`GET /api/provision/status` returns the connection, defaults, limits, and every site currently
counted against this connection:

```json
{
  "api_version": 1,
  "spawnwp_version": "0.5.37",
  "connection": {
    "id": "0123456789abcdef",
    "label": "Demo integration",
    "scope": "provision"
  },
  "defaults": {
    "expires_seconds": 3600,
    "role": "administrator",
    "group": "API",
    "access_profile": "standard",
    "credentials_mode": "return"
  },
  "limits": {
    "min_expires_seconds": 300,
    "max_expires_seconds": 31536000,
    "concurrent_sites": 3,
    "provision_timeout_seconds": 300
  },
  "active_sites": 1,
  "sites": [
    {
      "project": "customer-demo",
      "url": "https://wp.example.com/customer-demo",
      "status": "active",
      "expires_at": 1785402000,
      "created_at": 1785398400
    }
  ],
  "blueprints": [
    {"id": "development", "name": "Development", "version": "1", "source": "built-in"}
  ]
}
```

There is no separate `remaining` field. Compute available connection capacity as
`limits.concurrent_sites - active_sites`. A separate host-wide ceiling can still reject a
request before that connection limit is reached.

## Limits and errors

Each provisioning connection can own three concurrent sites by default. The server administrator
can set `SPAWNWP_PROVISION_MAX_SITES_PER_CONNECTION` from 1 to 100. A separate host-wide ceiling
may also apply.

| Status | Meaning | What the caller should do |
| --- | --- | --- |
| `400` | Invalid idempotency key or request value | Correct the request |
| `401` | Missing, expired, revoked, or invalid signature | Check clock and credentials; pair again if revoked |
| `403` | The connection has the wrong scope | Generate a provisioning pairing |
| `409` | Duplicate nonce, idempotency conflict, quota, or host operation in progress | Read `detail`; retry only transient conflicts |
| `422` | Body does not match the schema | Correct the listed field errors |
| `429` | Reverse-proxy request limit reached | Wait before retrying |
| `500` | Provisioning or cleanup failed | Record the response and inspect the cockpit |
| `504` | Upstream timeout | Retry with the same idempotency key to discover a stored result |
| `507` | Less than 3 GiB free on a required filesystem | Free disk space or use another host |

If a post-create step fails, SpawnWP removes the partial site. If that cleanup also fails, the
error includes the project and `status: "cleanup_required"` for operator intervention.

All FastAPI errors use the top-level `detail` property, never `message`. Most operational errors
contain a string:

```json
{"detail":"Project 'customer-demo' already exists"}
```

Request-schema failures return an array in `detail`, so custom clients must accept either form:

```json
{
  "detail": [
    {
      "type": "string_pattern_mismatch",
      "loc": ["name"],
      "msg": "String should match pattern '^[a-z0-9][a-z0-9-]{0,30}$'"
    }
  ]
}
```

If compensating cleanup also fails, `detail` is an object containing the underlying error,
project name, and `status: "cleanup_required"`. The official client formats all three forms.

## Implementing a custom client

Use the official client source as the reference implementation. A custom client must first
complete the pairing proof, then sign every provisioning request.

Send:

```text
X-SpawnWP-Connection: <connection_id>
X-SpawnWP-Timestamp: <current Unix timestamp>
X-SpawnWP-Nonce: <at least 16 unpredictable characters>
X-SpawnWP-Signature: <base64 Ed25519 signature>
```

`POST /api/provision` additionally requires:

```text
Idempotency-Key: <8-128 letters, digits, dots, colons, hyphens or underscores>
Content-Type: application/json
```

Build the canonical value from the HTTP method, raw URL path, Unix timestamp, nonce, and lowercase
SHA-256 of the exact body bytes:

```text
POST
/api/provision
<timestamp>
<nonce>
<sha256(body)>
```

Join those five lines with `\n`, encode as UTF-8, sign with the connection's Ed25519 private key,
and base64-encode the detached signature. Sign an empty body for `GET` and `DELETE`. Timestamps
allow ±300 seconds and every nonce is accepted only once.

Pairing uses the single-use bundle values and a client-generated key pair. The proof is an
Ed25519 signature over:

```text
pair|<pairing_id>|<source_public_key>|<source_host>
```

Send the proof to `POST /api/ingest/pair` with `pairing_id`, `token`,
`source_public_key`, `source_host`, and an optional `label`. Accept the connection only if the
response contains `scope: "provision"`.

Never log pairing codes, private keys, WordPress passwords, magic links, or complete successful
responses. Keep system clocks synchronized and use TLS verification in every environment.
