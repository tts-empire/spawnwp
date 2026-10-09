# SpawnWP website operations

The public site is static HTML under **website/**; MkDocs builds **docs/** into the
same release. GitHub validates and packages a preview, while the production server
publishes versioned directories behind **/var/www/spawnwp.com/public**.

## Validate and publish

    sudo bash ops/website/deploy-site.sh --check
    sudo bash ops/website/deploy-site.sh --publish

Publish requires a clean main branch equal to origin/main, successful **test** and
**site** workflows for HEAD, a valid Nginx configuration and root privileges. It
creates the next YYYYMMDD-website-v1.N directory, builds docs, generates the sitemap
from canonical HTML, flips the public symlink atomically and rolls back on a failed
live check. Signed plugin downloads stay outside the release tree. Historical releases
are not deleted automatically.

## WordPress.org plugin mirror

WordPress.org is the stable release authority for SpawnWP Deploy. The public plugin page
links to the official directory and latest-stable ZIP. A signed compatibility mirror at
`/downloads/spawnwp-deploy/` remains available for existing cockpit installations.

Install or refresh the root-only synchronizer on the production server with:

    sudo bash ops/website/install-plugin-sync.sh

The persistent systemd timer checks WordPress.org every ten minutes. It validates the
official ZIP, signs the exact bytes with `/root/.spawnwp/deploy-release-ed25519.pem`,
promotes the release atomically and updates `latest.json` last. A failed check leaves the
currently published mirror untouched. Inspect it with:

    sudo systemctl status spawnwp-plugin-sync.timer
    sudo journalctl -u spawnwp-plugin-sync.service
    sudo python3 /usr/local/lib/spawnwp/sync_wporg_plugin.py --check

Old `-dev` packages are retained for audit under
`/var/backups/spawnwp-plugin-previews/`, outside the Nginx download alias.

## SEO content map

| URL | Primary intent | Status |
|---|---|---|
| / | self-hosted WordPress sandbox and development lab | published |
| /wordpress-sandbox/ | self-hosted WordPress sandbox | published |
| /alternatives/instawp/ | self-hosted InstaWP alternative | published |
| /alternatives/localwp/ | LocalWP alternative for remote development | published |
| /alternatives/tastewp/ | self-hosted TasteWP alternative | published |
| /alternatives/easyengine/ | EasyEngine alternative for disposable WordPress sites | published |
| /alternatives/spinupwp/ | SpinupWP alternative for development and test sites | published |
| /use-cases/plugin-development/ | WordPress plugin development environment | published |
| /use-cases/wordpress-product-demos/ | WordPress product demos for themes and plugins | published |
| /guides/test-wordpress-multiple-php-versions/ | test WordPress plugin multiple PHP versions | published |
| /guides/wordpress-sandbox-vs-staging/ | WordPress sandbox vs staging | published |
| /guides/running-spawnwp-long-term/ | long-running environments, security and operations | published |
| /alternatives/wordpress-playground/ | WordPress Playground comparison | backlog |
| /guides/remote-wordpress-development/ | remote WordPress development | backlog |
| /guides/reusable-wordpress-blueprints/ | reusable WordPress blueprints | backlog |

Do not create empty stubs for backlog URLs. Add a page only when it has complete copy,
metadata, internal links and a real SpawnWP capability behind it.

## Comparison-page template

1. Give the direct answer in the first 100 words.
2. Explain both products and the operating model.
3. Add a factual comparison table.
4. Say when to choose the competitor and when to choose SpawnWP.
5. Disclose the fresh-server, two-hostname and administration requirements.
6. State SpawnWP's limits: not managed SaaS, production hosting or continuous staging.
7. Use SpawnWP screenshots only.
8. Include visible FAQs that match any FAQ schema.
9. Link official competitor sources and display the date checked.
10. End at requirements or installation, not a misleading instant-start CTA.

Re-check facts before every comparison change and at least quarterly. Immediate review
triggers include pricing, free-tier, expiry, hosting, export or self-hosting changes.

## Editorial calendar

- At WordPress beta and RC: update or prepare version-specific plugin test workflows.
- At each major WordPress release: confirm blueprint behavior and refresh relevant guides.
- During PHP beta and RC: draft compatibility content only after the runtime is available.
- After SpawnWP changes PHP, blueprints, lifecycle or QA tools: review affected claims.

## Measurement

Matomo site 6 is the operational analytics source. The shared script records page views,
**SEO Funnel** events for requirements, installation, GitHub and command-copy actions, and
**SEO Navigation** events for movement between comparison and category pages. The event
name is the page where the action happened. Product-demo pages additionally emit
`visit_product_demos` and `visit_demo_module_docs` so adoption can be separated from general
documentation traffic.

The `sc-domain:spawnwp.com` Search Console property is verified through DNS. Read-only OAuth
credentials power the private SEO snapshots and reports; keep them outside the repository,
rotate them if they appear in logs, and verify that the snapshot date continues to advance.
The `https://spawnwp.com/sitemap.xml` index is submitted and should report zero errors and
warnings. After reconnecting OAuth, confirm that status, then monitor indexed pages, queries,
CTR, average position and cannibalization together with Matomo conversions.

### SEO metadata release review

On 2026-10-09, PR [#19](https://github.com/tts-empire/spawnwp/pull/19) updated the title
and meta description on `/wordpress-sandbox/` and `/alternatives/localwp/`. The change is
live in release `20261009-website-v1.87` (main commit `9a095a388694fe498d9aa642ce751b1c97c2437b`).

Starting 2026-11-06, compare the 28 days after publication (2026-10-09 through 2026-11-05)
with the preceding 28 days (2026-09-11 through 2026-10-08). In Search Console, compare
clicks, impressions, CTR, average position and queries for each changed URL. In Matomo site 6,
compare page views and SEO Funnel actions from those landing pages. Use the latest complete
Search Console data and note any other page or campaign changes that affect interpretation.
