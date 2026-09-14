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
| /wordpress-sandbox/ | self-hosted WordPress sandbox | published |
| /alternatives/instawp/ | self-hosted InstaWP alternative | published |
| /alternatives/localwp/ | LocalWP alternative for remote development | published |
| /alternatives/tastewp/ | self-hosted TasteWP alternative | published |
| /use-cases/plugin-development/ | WordPress plugin development environment | published |
| /use-cases/wordpress-product-demos/ | WordPress product demos for themes and plugins | published |
| /guides/test-wordpress-multiple-php-versions/ | test WordPress plugin multiple PHP versions | published |
| /guides/wordpress-sandbox-vs-staging/ | WordPress sandbox vs staging | published |
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

### Demo and installation measurement (2026-09-14)

The website measures intent, not successful server installations. Existing `SEO
Funnel` actions remain compatible. `open_install_section` records the home CTA;
`copy_install_command` is emitted only after copying succeeds, both on the home
page and on the three explicitly marked installation-guide blocks. Other code
examples do not count. Events contain the source pathname, never copied commands.

Material's `document$` drives docs pageviews: update URL/title/referrer once per
page navigation, including back/forward, and ignore anchor-only changes. The
tracker must be loaded after Material and must dispatch through the current
`window._paq`, because Matomo replaces its bootstrap queue when it loads.

The home video emits `Demo Video` actions `demo_video_start`, `demo_video_50`, and
`demo_video_complete` once each per page load. The 50% threshold uses the union of
actually played ranges, not the seek position. Complete means the player reached
its end; it does not claim the entire video was watched. No paid media plugin is
required, and tracking failures never prevent copying or playback.

Preview the four site-6 goals with:

```bash
python3 ops/website/configure_matomo_goals.py
```

Apply the preview using a config with sufficient Matomo Write/Admin access:

```bash
python3 ops/website/configure_matomo_goals.py --config /path/to/private-config.json --apply
```

The config has the same `url`, `token_auth`, `id_site` fields as the read-only
report config. Keep it outside the repository and never pass the token on the
command line. Goals match installation-guide pageviews, successful command copy,
video 50%, and either modules or Turnstile documentation. Each allows one
conversion per visit with zero revenue. The script checks all matches before
writing, reuses existing IDs, refuses conflicting definitions and records an
activation date in each managed goal description. Retrying after partial failure
does not create duplicates. No manual `trackGoal` calls or historical backfill.

`matomo_report.py` reports full event totals independently of the 25 displayed
source-page rows. Rates use visits with the action, not repeated event counts.
New actions before the rollout date and goals before activation are unavailable;
intervals including activation day are partial and have no comparison rate.
The private outreach reporter ships a copy of `matomo_measurement.py`; update it
alongside this module. Snapshot JSON keeps legacy funnel keys and stores the
additional metrics under `_measurement`, requiring no database migration.

Browser verification runs on generated HTML (including real Material navigation):

```bash
pip install -r website/requirements-test.txt
playwright install chromium
SPAWNWP_ANALYTICS_TEST_ROOT=/path/to/public-site \
  python3 -m unittest discover -s ops/website -p test_analytics_browser.py -v
```

The tests intercept external requests. An optional `SPAWNWP_MATOMO_TEST_JS` path
to a downloaded public Matomo tracker enables its real request-payload check;
those requests are also intercepted and never reach production. The site CI
runs browser tests on its preview artifact. Production deployment uses
`deploy-site.sh --publish` after the `site` and `test` workflows pass for main.
Rollback uses the preceding release symlink. Managed goals can remain configured
during rollback; unavailable events simply stop producing conversions.
