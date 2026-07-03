---
description: Internal implementation notes and gotchas for the 0.4.0 content-blueprints feature.
---

# Content blueprints — implementation notes (internal)

> Internal development notes from the 0.4.0 implementation. Not part of the public
> MkDocs navigation.

## Source table prefix is derived, not declared

The blueprint manifest schema v2 deliberately carries **no `source_prefix` field**
(unlike the site-to-site deploy manifest). `runtime/scripts/import-database.php`
derives it from the export itself: it scans the `{"type":"table"}` records and picks
the table ending in `options` that has a sibling `posts` table with the same prefix.
Exactly one candidate must match, otherwise the import aborts. This is robust against
plugin tables like `wp_plugin_options` (no `wp_plugin_posts` sibling) but assumes
single-site captures — which the plugin's guard already enforces. If schema v3 ever
adds fields, prefer keeping the manifest free of source-identifying data (privacy:
the manifest must not reveal the source site).

## nginx changes reach existing hosts via a migration

`installer/nginx.conf.tpl` only affects fresh installs. Existing hosts get the
rate-limited `/api/ingest/` location through
`installer/migrations/add-ingest-nginx-location.py`, which runs on **every** update
(all migrations do), so it is idempotent (no-op when `zone=spawnwp_ingest` is already
present). Two anchoring details learned the hard way:

- Older live configs (pre `spawnwp_auth` location era, e.g. long-lived dev hosts) do
  **not** contain the `location ~ ^/api/auth/…` block, so the migration anchors on
  the `location /assets/ { … }` block instead — the only block identical across all
  config generations — and inserts the ingest location right after it. The
  `limit_req_zone` line anchors after the existing `spawnwp_auth` zone.
- The location includes `/etc/nginx/snippets/spawnwp-proxy.conf`, which only
  `install.sh` installs; hosts that were installed before that snippet existed and
  then updated would fail `nginx -t`. The migration therefore installs the snippet
  from the release payload (a sibling of `migrations/` inside `payload/lib/installer/`)
  when it is missing. On any `nginx -t`/reload failure the original config is restored
  and reloaded.

A migration needs registering in **three** places: `updater/managed-files.json`
(`installer` list), and in `updater/build-release.py` both the executable-mode set
and the manifest `migrations` list. New cockpit Python modules (`ingest.py`,
`machine_auth.py`) also had to be added to the non-`static/` target set in
`build-release.py`, or they would be published under the web-served `static/` tree.

## Ports on the development host

On this development VPS, `127.0.0.1:9494` is the **live telemetry receiver**
(`spawnwp-telemetry-receiver.service`), not a scratch port — the cockpit is on 9393.
When running a second cockpit instance for end-to-end tests, use another port (9595
was free) and drive it with `SPAWNWP_INGEST_DB`, `SPAWNWP_BLUEPRINT_PAYLOADS`,
`SPAWNWP_CUSTOM_BLUEPRINTS`, `SPAWNWP_BLUEPRINT_TOOL`, `SPAWNWP_METRICS_FILE` and
`SPAWNWP_VERSION_FILE` environment overrides.

## Other contract notes worth remembering

- **Atomicity contract**: the manifest in `/etc/spawnwp/blueprints.d/` is the only
  thing `blueprint.py discover()` reads, and the ingest writes it **last**
  (tmp + `os.replace`) after the payload is fully verified and moved into place.
  Payload files are per-job (`payload-<job8>.zip`), so a replace keeps the old
  payload until the new manifest lands, then deletes stale `payload*.zip`.
- The placeholder URL `https://blueprint.spawnwp.invalid` is duplicated by design in
  three places that cannot share code: the plugin
  (`SpawnWP_Deploy_Blueprint::PLACEHOLDER_URL`), `apply-content-blueprint.sh`
  (`PLACEHOLDER_URL`) and the docs. Keep them in sync.
- Cross-implementation signature drift is guarded by
  `runtime/tests/fixtures/machine-auth-vectors.json` (generated once with PHP
  sodium) asserted by `test_machine_auth.py`; regenerate the fixture only if the
  canonical string format ever changes, together with `ingest_format` bumping.
- The capture passes `keep_deploy_plugin => false` so `active_plugins` in the
  captured database does not reference `spawnwp-deploy/spawnwp-deploy.php` (spawned
  sites do not ship the plugin binaries — `DEV_PLUGINS` excludes them from the zip).
- FastAPI error bodies use `detail`, WordPress-style ones use `message`; the
  plugin's `decode_response()` reads both, and maps a bare 404 to a friendly
  "update your SpawnWP server to 0.4.0+" message.
