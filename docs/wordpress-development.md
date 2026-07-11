---
description: Develop and test WordPress plugins and themes with SpawnWP, its QA tools, Mailpit, Adminer and WP-CLI.
---

# WordPress development

When a project is finished, use any backup, export, migration or publishing workflow
you prefer. The [optional SpawnWP Deploy WordPress plugin](deploying-a-site.md) is one
way to copy a site once to a separate, fresh WordPress installation; it is not required
by SpawnWP and is not a continuous staging synchronization tool.

Every spawned site is a ready-to-use environment for building WordPress plugins and
themes — especially ones headed for the **WordPress.org** directory.

## Where your code lives

Plugin and theme source is a **host bind mount**, so you edit it with your own tools and
it shows up in the container instantly:

```text
/srv/<site>/projects/primary/wp-content/plugins/<your-plugin>/
/srv/<site>/projects/primary/wp-content/themes/<your-theme>/
```

Clone your repos straight into those folders. `WP_DEBUG` and the debug log are enabled,
so notices and errors surface while you work.

## In-WordPress QA (the .org checks, in the browser)

The default Development blueprint preinstalls the official review tools, surfaced from the **🛠 Dev toolkit**
dashboard widget:

- **Plugin Check** — runs the same automated checks as the WordPress.org plugin review
  (WordPress Coding Standards via PHPCS, security, internationalization, forbidden
  functions…). Go to **Tools → Plugin Check**, pick your plugin, run.
- **Theme Check** — the equivalent for themes (**Appearance → Theme Check**).
- **Query Monitor** — runtime debugging: database queries, hooks, HTTP API calls, PHP
  errors, template loading. Visible to logged-in admins.
- **User Switching** and **WP Crontrol** — switch roles to test capabilities; inspect and
  run WP-Cron events.

## CLI QA toolchain (headless / CI)

The PHP container ships command-line tools for scripting and CI. Run them from the site
directory:

!!! tip "WP-CLI without SSH"
    For `wp` commands specifically you don't need a host shell: every site card in
    the cockpit has a [WP-CLI console](using-the-cockpit.md#the-wp-cli-console) that
    runs them inside the container and streams the output in the browser.

=== "Coding standards (phpcs / WPCS)"

    ```bash
    cd /srv/<site>
    docker compose exec -u www-data php phpcs path/to/plugin
    docker compose exec -u www-data php phpcbf path/to/plugin   # auto-fix
    ```
    `phpcs` defaults to the **WordPress** standard. The same checks are also available
    in-browser via Plugin Check.

=== "PHP cross-version compatibility"

    ```bash
    docker compose exec -u www-data php \
      phpcs --standard=PHPCompatibilityWP --runtime-set testVersion 8.0- path/to/plugin
    ```

=== "Static analysis (PHPStan + WP stubs)"

    ```bash
    docker compose exec -u www-data php \
      phpstan analyse -c /usr/local/etc/phpstan-wp.neon --memory-limit=512M path/to/plugin
    ```

## Email testing

WordPress is wired to **Mailpit**: every email the site sends (password resets, order
notifications, your plugin's mails) is captured instead of delivered. Open it from the
cockpit's **✉️ Mailpit ▸** button to inspect content, headers and HTML rendering.

## PHP versions & Xdebug

- Switch a site's PHP version from the cockpit (**PHP ▾**) or the CLI
  (`make php-switch VER=8.4`). Cached versions switch instantly.
- PHP 7.4 is an explicitly legacy, end-of-life option for compatibility work only.
  Keep PHP 8.3 or newer for new projects and public production sites.
- Toggle Xdebug with `make xdebug-on` / `make xdebug-off` (listens on port 9003; point
  your IDE at it over an SSH tunnel). It is off by default, so it costs you nothing until
  you ask for it.

## PHP extensions

Every site gets the same set on **every** PHP version (7.4, 8.2, 8.3, 8.4), so switching
version never changes what your code can call.

| Area | Extensions |
|------|------------|
| Database | `mysqli`, `pdo_mysql`, `pdo_sqlite`, `sqlite3`, `mysqlnd` |
| Images | `gd` (JPEG + WebP), `imagick`, `exif` |
| Web & data | `curl`, `openssl`, `sodium`, `json`, `dom`, `simplexml`, `xml`, `xmlreader`, `xmlwriter`, `libxml`, `soap` |
| Text & i18n | `mbstring`, `iconv`, `intl`, `ctype`, `pcre` |
| Files & archives | `zip`, `zlib`, `fileinfo`, `phar` |
| Maths | `bcmath`, `gmp` |
| Networking | `ftp` (with FTPS), `sockets` |
| Process & time | `pcntl`, `posix`, `calendar` |
| Caching & perf | `opcache`, `redis` |
| Debugging | `xdebug` (installed, disabled by default — see above) |

To see the exact list for a running site, open its **⌨ WP-CLI** console in the cockpit and run:

```
eval "print_r(get_loaded_extensions());"
```

### FTP is outbound only

`ftp` gives your site's PHP code the **client** functions — `ftp_connect()` and
`ftp_ssl_connect()` — so plugins can reach **out** to a remote FTP/FTPS server (backup plugins
pushing archives off-site, migration plugins pulling files in). FTPS is enabled on every PHP
version.

It does **not** make your site reachable *over* FTP. There is no FTP or SFTP daemon in the
stack, and every container port binds to loopback. To work on a site's files, use the cockpit's
**📂 Files** browser or the **⌨ WP-CLI** console.

### Not available

`imap` — removed from PHP core in 8.4, so it is not installable across the versions we build.

See the [CLI reference](cli-reference.md) for all `make` targets.
