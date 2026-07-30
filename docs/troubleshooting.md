---
description: Diagnose common SpawnWP installation, DNS, TLS, authentication, container and WordPress environment problems.
---

# Troubleshooting

## The cockpit redirects to login

The cockpit, Adminer and Mailpit require an active SpawnWP session. Sign in again. If
the account factors are unavailable, use `sudo spawnwp auth reset` from the server.

## Installation fails at the certificate step

Let's Encrypt validates over HTTP on port 80 for **both** hostnames. Common causes:

- **DNS not propagated** — `dig +short DOMAIN` and `dig +short COCKPIT_DOMAIN` must both
  return the server IP *before* installing. See [DNS setup](dns-setup.md).
- **Port 80 blocked** — open 80 (and 443) in any cloud firewall/security group.
- **Cloudflare proxy on** — set the records to "DNS only" (grey cloud) for the install.

Once DNS/ports are fixed, re-run the certificate step (the installer is idempotent for
this) or `certbot --nginx -d DOMAIN -d COCKPIT_DOMAIN`.

## A new site is half-up / shows 502

On a site's **first** start, WordPress extracts thousands of files into an empty volume;
under that I/O the PHP health-check can be slow and nginx may briefly 502. Give it a
minute, then `make up` again from the site directory. The cockpit's two-phase startup
normally handles this for you.

## A new site came up on an old WordPress version

SpawnWP always rebuilds the PHP image with `--pull` when spawning, so new sites get the
latest WordPress. If you created sites with an older build, update in place:

```bash
cd /srv/<site>
docker compose exec -u www-data php wp core update
docker compose exec -u www-data php wp theme update --all
```

## `/<site>/wp-admin` redirects to the main site

Always use the trailing slash: `/<site>/wp-admin/`. SpawnWP's nginx already rewrites the
no-slash form, but if you customized the vhost, ensure the per-site `proxy_redirect` line
is present.

## A plugin link loses the `/<site>/` prefix

SpawnWP serves each WordPress environment below `https://DOMAIN/<site>/`. It sets
`WP_HOME` and `WP_SITEURL`, forwards the external prefix and restores it in the WordPress
request URI. WordPress core and plugins that build URLs with helpers such as `admin_url()`,
`home_url()` and `rest_url()` therefore retain the site prefix.

A plugin that hardcodes a root-relative URL such as `/wp-admin/admin.php` or `/wp-json/`
bypasses those values. The browser leaves the current environment and requests the root
of `DOMAIN`, which can appear as a 404, a different site, or **WordPress environment not
running**. Compare the broken URL with the expected
`/<site>/wp-admin/...` path in the browser's address bar or Network panel.

Report the root-relative URL to the plugin author and ask them to use the corresponding
WordPress URL helper or a server-provided localized URL. SpawnWP deliberately does not
rewrite arbitrary HTML and JavaScript responses: with multiple independent sites on one
hostname, a global rewrite could send a request to the wrong environment or alter
legitimate application paths.

## Port already allocated

Each site claims the next free loopback ports. If a manual container or another service
grabbed one, free it (or stop the conflicting container) and retry.

## Cockpit unreachable after a reboot

Check the services came back:

```bash
systemctl status docker wp-cockpit nginx
```

Start any that are down with `systemctl start <unit>`.

## Where are my credentials?

`/root/spawnwp-credentials.txt` (root-readable). It contains the URLs, initial application
activation code and WordPress admin login.
