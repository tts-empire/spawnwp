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
| `spawnwp-api revoke --yes` | Revoke the server connection and remove local credentials |

### Provisioning options

| Option | Default | Description |
| --- | --- | --- |
| `--blueprint ID` | required | Blueprint to use, for example `development` |
| `--expires DURATION` | `1h` | `300`–`31536000` seconds, or a value such as `30m`, `2h`, `7d` |
| `--role ROLE` | `administrator` | Initial WordPress user role |
| `--group NAME` | `API` | Cockpit group for the site |
| `--name SLUG` | generated | Optional stable project name |
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
| `DELETE /api/provision/connection` | Revoke the calling connection | no |

### Create request

Only `blueprint` is required:

```json
{
  "blueprint": "development",
  "expires_seconds": 3600,
  "role": "administrator",
  "group": "API",
  "name": "customer-demo"
}
```

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
