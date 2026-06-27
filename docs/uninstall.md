# Uninstall

spawnwp installs into `/srv`, the host nginx config and systemd. When port-knocking is
enabled it also configures knockd. To remove it,
work from the outside in. **Back up anything you want to keep first** (snapshots,
databases, `wp-content`).

!!! danger "Destructive"
    These steps delete all sites, databases and volumes. There is no undo.

## 1. Destroy the sites

From the cockpit, bring each site **Down** and **Destroy** it. Or from the shell:

```bash
for d in /srv/*/; do
  [ -f "$d/compose.yaml" ] || continue
  (cd "$d" && docker compose down -v --remove-orphans)
done
```

## 2. Remove the project directories

```bash
rm -rf /srv/wp-dev /srv/wp-cockpit /srv/*   # (only spawnwp dirs live under /srv)
```

## 3. Remove host services

```bash
systemctl disable --now wp-cockpit.service knockd.service
systemctl disable --now cockpit-reaper.timer docker-prune.timer
rm -f /etc/systemd/system/wp-cockpit.service \
      /etc/systemd/system/cockpit-reaper.* \
      /etc/systemd/system/docker-prune.*
systemctl daemon-reload
```

## 4. Remove nginx config

Restore your own nginx `default` site (or remove spawnwp's server blocks for `DOMAIN` and
`COCKPIT_DOMAIN`), then:

```bash
nginx -t && systemctl reload nginx
```

## 5. Remove knock config and secrets

```bash
rm -f /etc/knockd.conf /etc/nginx/cockpit-allowed.conf /etc/nginx/.htpasswd
rm -rf /etc/knockd /run/cockpit-sessions
rm -f /root/spawnwp-credentials.txt
```

## 6. (Optional) Certificate and packages

```bash
certbot delete --cert-name DOMAIN     # remove the TLS certificate
```

Docker, nginx and certbot are left installed in case you use them for other things;
knockd is also left installed if that option was enabled. Remove packages only if you
are sure they are unused.
