# SpawnWP telemetry receiver

Minimal receiver for explicit, 90-day SpawnWP telemetry consent. It binds to
`127.0.0.1:9494`, stores one HMAC-pseudonymized latest-state row per installation in
SQLite, and never stores request IP addresses or raw event history.

Production deployment requires a dedicated system user, a root-owned HMAC key readable
by the service group, the included systemd units, and the included Nginx rate-limit and
location snippets. Uvicorn access logging must remain disabled.

Run `/usr/local/sbin/spawnwp-telemetry-report` as root for local aggregate summaries.
The public endpoint is intentionally unauthenticated, so results are directional rather
than suitable for billing, security decisions or authoritative usage counts.

`email_report.py` can send that aggregate report through an authenticated SMTP account.
Its JSON configuration belongs outside the repository at
`/etc/spawnwp-telemetry-mail.json`, must be readable only by root, and requires the keys
`host`, `port`, `security` (`starttls` or `ssl`), `username`, `password`, `from` and `to`.
Schedule it as root and use a lock so overlapping deliveries cannot occur.
