---
description: Safely uninstall SpawnWP and understand which environments, volumes and server files will be removed.
---

# Uninstall

SpawnWP installs into `/srv`, the host nginx config and systemd. To remove it, work from
the outside in. **Back up anything you want to keep first** (snapshots,
databases, `wp-content`).

!!! danger "Destructive"
    These steps delete all sites, databases and volumes. There is no undo.

## 1. Destroy the sites

From the cockpit, bring each site **Down** and **Destroy** it. Or from the shell:

```bash
for d in /srv/*/; do
  [ -f "$d/compose.yaml" ] && [ -f "$d/.env" ] || continue
  (cd "$d" && docker compose down -v --remove-orphans)
done
```

## 2. Remove the project directories

```bash
rm -rf /srv/wp-dev /srv/wp-cockpit /srv/*   # (only spawnwp dirs live under /srv)
```

## 3. Remove host services

```bash
systemctl disable --now wp-cockpit.service docker-prune.timer
rm -f /etc/systemd/system/wp-cockpit.service \
      /etc/systemd/system/docker-prune.*
systemctl daemon-reload
```

## 4. Remove nginx config

Restore your own nginx `default` site (or remove SpawnWP's server blocks for `DOMAIN` and
`COCKPIT_DOMAIN`), then:

```bash
nginx -t && systemctl reload nginx
```

## 5. Remove secrets and application files

```bash
rm -rf /etc/spawnwp /var/lib/spawnwp /opt/spawnwp /usr/local/lib/spawnwp
rm -f /usr/local/bin/spawnwp /root/spawnwp-credentials.txt
```

## 6. (Optional) Certificate and packages

```bash
certbot delete --cert-name DOMAIN     # remove the TLS certificate
```

Docker, nginx and certbot are left installed in case you use them for other things.
Remove packages only if you are sure they are unused.
