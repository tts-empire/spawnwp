---
description: Install, inspect, update and remove signed optional SpawnWP modules.
---

# Optional modules

SpawnWP modules add product-specific workflows without turning the open-source core into a
monolith. They are installed explicitly by the server administrator and appear under **Modules**
in the cockpit. The core remains responsible for authentication, blueprints, provisioning,
expiry, cleanup and update safety.

## Trust and layout

A module release consists of three adjacent files:

```text
MODULE-VERSION.tar.gz
MODULE-VERSION.manifest.json
MODULE-VERSION.manifest.sig
```

`spawnwp module install` verifies the Ed25519 signature, archive checksum, every packaged file,
the module identifier and its supported core-version range before activation. Archives containing
path traversal, symlinks, hard links or device files are rejected. Releases live under
`/opt/spawnwp/modules/ID/releases/VERSION`; an atomic `current` symlink selects the active one.
Persistent module state belongs under `/var/lib`, not inside the release directory.

A signed module may request one least-privilege core API connection with the optional
`core_api_scope` manifest field. SpawnWP creates that local connection during installation,
stores its credential root-only and revokes it when the module is removed. Manual pairing remains
for integrations running outside the server; local modules must not read the cockpit database.

Only install packages received from SpawnWP or another publisher whose key you deliberately added
to your trust policy. A valid signature authenticates the publisher; it does not sandbox the
module. Install hooks run as root because they may add systemd units and an nginx route.

## Commands

```bash
sudo spawnwp module install ./demo-launcher-0.1.0.tar.gz
sudo spawnwp module status
sudo spawnwp module update demo-launcher --source ./demo-launcher-0.1.1.tar.gz
sudo spawnwp module remove demo-launcher
sudo spawnwp module remove demo-launcher --purge
sudo spawnwp module enable demo-launcher
sudo spawnwp module disable demo-launcher
```

An update reuses the recorded HTTPS package URL when possible. A module originally installed from
a local file needs a new `--source`. Removal runs the module's uninstall hook and deactivates its
release; module-owned state is preserved unless that module documents otherwise. `--force` is
available for a module that refuses normal removal while work is active. Modules may also expose
an explicit purge mode: `--purge` asks the module to permanently remove its own configuration and
database as part of uninstall. Purge is deliberately separate from normal removal and may be
blocked while active resources still exist.

Modules may provide signed `activate.py` and `deactivate.py` hooks. When present, the Cockpit
shows Enable and Disable controls in addition to Manage, Update and Uninstall. Disable leaves the
release and module data in place, stops its integration and revokes its local core API credential;
Enable issues the credential again and restores the integration. Older modules without these
hooks remain compatible and simply do not expose Enable/Disable.

Cockpit lifecycle operations run asynchronously. Their progress is available from
`/api/modules/operations/{operation_id}` and all mutating requests require session, CSRF and
recent passkey authentication.

## Free marketplace

The Cockpit Marketplace reads a small, signed catalog from
`https://spawnwp.com/modules/catalog.json` (signature:
`catalog.sig.b64`). The catalog is curated by SpawnWP and currently contains free modules only.
Each entry points to a GitHub Release `.tar.gz`; the matching signed manifest and signature are
downloaded automatically and verified by the same module trust key before installation. The
browser never submits an arbitrary package URL: the server resolves the selected module from the
verified catalog and starts the normal asynchronous module install operation.

The catalog is optional. If it is unavailable or its signature is invalid, installed modules and
the manual upload/CLI workflow remain available. Operators can override the catalog endpoint with
`SPAWNWP_MODULE_CATALOG_URL` and `SPAWNWP_MODULE_CATALOG_SIGNATURE_URL` when testing a mirror.

## Demo Launcher beta

Demo Launcher is the first optional, separately developed module. It turns a captured blueprint
into a campaign URL suitable for a theme or plugin product page:

For the product-page workflow and its operational boundaries, see the [WordPress product demos
use case](https://spawnwp.com/use-cases/wordpress-product-demos/).

1. a visitor opens the URL without creating a site;
2. an explicit request passes CSRF and Cloudflare Turnstile checks;
3. per-source and per-campaign limits admit the request to a durable queue;
4. a single worker provisions an expiring site with managed credentials and a restricted
   administrator profile;
5. the visitor receives a short-lived front-end URL and can request a single-use admin link.

Campaign state contains no reusable WordPress password and no email address. Source addresses are
stored only as keyed HMACs for rate limiting and removed after 24 hours. Turnstile remains a
third-party Cloudflare service, so the operator must disclose and configure that processing as
required for its visitors. Disabling the module does not destroy sites already owned by the core;
their normal expiry remains authoritative.

The beta module is distributed separately from the MIT-licensed core under its own terms. Keeping
that boundary explicit lets the core stay useful and auditable while advanced product workflows
can follow a different release and commercial model.
