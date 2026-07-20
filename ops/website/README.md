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

Matomo site 6 is the operational analytics source. The shared script records page views
and **SEO Funnel** events for requirements, installation, GitHub and command-copy actions;
the event label is the landing path.

Search Console is not currently verified. When credentials or a verification token become
available, verify the property, submit https://spawnwp.com/sitemap.xml, and monitor indexed
pages, queries, CTR and cannibalization.
