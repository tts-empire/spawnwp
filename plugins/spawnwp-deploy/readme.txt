=== SpawnWP Deploy ===
Contributors: wpvoicer
Tags: deployment, migration, staging, development, blueprint
Requires at least: 6.8
Tested up to: 7.0
Stable tag: 0.3.4
Requires PHP: 7.4
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Capture a configured site as a reusable blueprint, or publish it once to a separate empty WordPress installation.

== Description ==

SpawnWP Deploy provides two administrator-initiated workflows:

* Capture a configured WordPress site as a reusable blueprint on a self-hosted SpawnWP server.
* Publish a finished site once to a separate, fresh WordPress installation.

It is not a continuous staging or synchronization system. Site-to-site deployment is
limited to an empty destination and keeps rollback data for seven days. Connections use
single-use pairing codes, signed requests and replay protection.

For blueprint capture, the administrator chooses whether to include plugins, themes,
uploads and database content. WordPress users and user metadata are excluded. The source
site URL is replaced with a fixed placeholder in captured database content, and each site
created from the blueprint receives its own administrator credentials.

= Requirements =

* WordPress single-site.
* PHP 7.4 or later with the Sodium and ZIP extensions.
* HTTPS with a publicly trusted certificate on paired endpoints.
* A reachable WordPress REST API without an additional HTTP password.
* Write access to the WordPress plugin, theme and uploads directories.
* Sufficient free disk space for staging and rollback data.

Always back up important sites before using deployment or capture tools.

== External Connections ==

This plugin does not contact a SpawnWP-operated SaaS platform and does not send telemetry
to SpawnWP merely because it is installed, activated or loaded.

An administrator can explicitly pair the plugin with either:

1. another WordPress site running SpawnWP Deploy; or
2. a self-hosted SpawnWP server whose URL is supplied by the administrator.

During a requested deployment or capture, the plugin sends the selected package and
technical metadata to that administrator-selected endpoint. Depending on the selected
options, this can include plugin and theme files, media uploads, database content,
WordPress and PHP versions, plugin names and versions, package checksums, job identifiers
and connection signatures. WordPress user and user-meta tables are excluded.

The remote endpoint is operated by the site administrator or their chosen provider. Its
privacy and retention practices are therefore controlled by that operator. SpawnWP is
self-hosted open-source software distributed under the MIT License:

* SpawnWP project: https://spawnwp.com/
* Source code and license: https://github.com/tts-empire/spawnwp
* Security documentation: https://spawnwp.com/docs/security/
* Optional SpawnWP platform telemetry notice: https://spawnwp.com/privacy/telemetry/

When the plugin inventory is displayed and WordPress does not already have cached update
information for an active plugin, SpawnWP Deploy may query the WordPress.org Plugins API
using WordPress core's `plugins_api()` function. It sends the plugin slug and uses the
response only to classify the plugin as WordPress.org-hosted or custom. The result is
cached for one day. WordPress.org privacy policy: https://wordpress.org/about/privacy/

== Installation ==

1. Install and activate SpawnWP Deploy.
2. Open Tools > SpawnWP Deploy.
3. Choose the workflow that applies to the current site.
4. Generate or paste the single-use pairing code at the administrator's request.
5. Review the selected content and confirmations before starting the operation.

== Frequently Asked Questions ==

= Does this continuously synchronize two sites? =

No. It performs a one-time publish to an empty destination. It does not merge later
changes, orders, comments, form submissions or users.

= Is a SpawnWP server required? =

Only for reusable blueprint capture. A one-time site-to-site publish uses SpawnWP Deploy
on both WordPress installations.

= Does the plugin send data automatically? =

No deployment or capture payload is sent automatically. An administrator must pair an
endpoint and explicitly start the operation. A cached WordPress.org plugin-information
lookup may occur when an administrator opens the blueprint inventory, as documented in
External Connections.

= Are WordPress users copied? =

No. User and user-meta tables are excluded. On deployment, ownership of imported content
is assigned to the administrator of the destination site.

= Can I deploy over an existing site? =

No. The destination guard rejects sites containing application content or unexpected
active plugins. Use a dedicated backup or migration product when merging into an active
site.

= Where is temporary data stored? =

Packages and receiver workspaces are stored below the WordPress uploads directory in
`spawnwp-deploy/`, protected from direct web access, and removed after completion or
expiry. Rollback data is retained for up to seven days.

== Screenshots ==

1. Create a reusable blueprint from a configured WordPress site.
2. Review connected SpawnWP servers and licensed or custom plugin warnings.
3. Publish a finished site to a paired empty WordPress destination.
4. Generate a short-lived connection key on an eligible destination.

== Changelog ==

= 0.3.4 =

* Added remembered blueprint capture fields and safe patch-version suggestions.
* Added a clear reset flow for starting a different blueprint.
* Prepared the plugin for WordPress.org distribution and validation.

== Upgrade Notice ==

= 0.3.4 =

Initial WordPress.org release candidate.
