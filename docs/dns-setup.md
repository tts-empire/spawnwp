# DNS setup

SpawnWP uses **two hostnames you choose**, both pointing at your server:

| Variable | Example | Serves |
|---|---|---|
| `DOMAIN` | `dev.example.com` | WordPress sites (`/` and `/<site>/`) |
| `COCKPIT_DOMAIN` | `cockpit.example.com` | The cockpit + per-site Adminer/Mailpit |

You can pick any names you control. Common patterns:

- **Sibling subdomains:** `dev.example.com` + `cockpit.example.com`
- **Root + subdomain:** `example.com` + `cockpit.example.com`
- **Two unrelated names**, as long as both resolve to the same server.

!!! warning "Both must resolve before you install"
    During installation, SpawnWP obtains a single Let's Encrypt certificate covering
    **both** hostnames using HTTP validation on port 80. If either name does not yet
    point at the server, certificate issuance fails. Set up DNS first and let it propagate.

## 1. Find your server's public IP

```bash
curl -4 https://ifconfig.me      # IPv4
curl -6 https://ifconfig.me      # IPv6 (optional)
```

## 2. Create the DNS records

In your DNS provider, add an **A record** (IPv4) for each hostname pointing at the
server IP. If your server has IPv6, optionally add **AAAA records** too.

| Type | Name | Value |
|---|---|---|
| A | `dev` (for `dev.example.com`) | `<server IPv4>` |
| A | `cockpit` (for `cockpit.example.com`) | `<server IPv4>` |
| AAAA *(optional)* | `dev` | `<server IPv6>` |
| AAAA *(optional)* | `cockpit` | `<server IPv6>` |

!!! tip "Cloudflare users"
    Set the records to **DNS only** (grey cloud), not proxied, at least for the initial
    install so Let's Encrypt HTTP validation reaches your server directly. You can enable
    the proxy afterwards if you wish.

## 3. Verify propagation

From your laptop (or the server):

```bash
dig +short dev.example.com
dig +short cockpit.example.com
# Each should print your server IP.
```

or:

```bash
getent hosts dev.example.com cockpit.example.com
```

Propagation is usually quick but can take from a few minutes up to an hour depending on
your provider and TTL. Once both names return the server IP, continue to
[Installation](installation.md).
