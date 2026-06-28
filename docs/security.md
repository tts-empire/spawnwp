# Security

## Optional deployment plugin

[SpawnWP Deploy](deploying-a-site.md) uses per-connection Ed25519 keys, signed requests,
timestamps, nonces, body hashes and checksummed chunks. Connection keys expire after
15 minutes. The target stages and verifies the package before activation and retains a
seven-day rollback. The plugin is a public preview and is not installed by default.

SpawnWP is a self-hosted development lab, not a production hosting control panel. The
security model keeps services private, encrypts browser traffic and requires strong
application authentication.

## Access model

| Surface | Protection |
|---|---|
| Cockpit | HTTPS, mandatory SpawnWP session, CSRF and rate limiting |
| Adminer / Mailpit | HTTPS plus Nginx `auth_request` to the SpawnWP session |
| WordPress sites | Public HTTPS, with normal WordPress authentication for `/wp-admin` |
| Databases and container services | Bound to loopback or private Docker networks |

The cockpit supports passkeys as the preferred login. Signing in without one requires a
strong password plus TOTP or a single-use recovery code. Authentication state is server-side;
credentials, TOTP secrets and recovery material are never stored in plaintext.

Nginx rate-limits enrollment and login ceremonies before requests reach the application.
The application applies its own per-source attempt limits, challenge expiry, replay
protection and audit logging.

## Host and container boundaries

- TCP ports 80 and 443 need public ingress.
- nginx terminates TLS and proxies to loopback-bound services.
- Adminer, Mailpit, databases and PHP services are not public network endpoints.
- Containers do not receive the Docker socket.
- Generated database and WordPress credentials are unique per environment.
- PHP switching, restore, destroy and control-plane updates require authentication
  within the previous ten minutes. When that window expires, the cockpit requests an
  inline Passkey confirmation and resumes the action only after successful verification.

The cockpit service runs as root because it orchestrates host Docker and Nginx state.
This makes cockpit compromise equivalent to host compromise: keep SpawnWP updated,
protect the administrator factors and do not install it on a server containing unrelated
production workloads.

## Operational requirements

- Keep automatic OS security updates enabled or patch the host regularly.
- Keep the provider firewall limited to 80, 443 and administrative SSH sources.
- Store recovery codes and the credentials report in a password manager.
- Remove expired or revoked access credentials promptly.
- Review authentication audit events after unexpected login failures.

SpawnWP deliberately does not provide email hosting, DNS management, tenant isolation or
managed-hosting guarantees. WordPress environments are intended to be disposable and
must not be treated as the only copy of valuable data.
