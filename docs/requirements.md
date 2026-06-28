# Requirements

You need a fresh server and two hostnames. The installer handles everything else:
Docker, nginx, certificates, the cockpit and the first WordPress
environment.

You do **not** need to preinstall Docker, write nginx config, open custom admin
ports, or remember server commands for normal use. After setup, the cockpit is the
main interface.

## Server

| | |
|---|---|
| **OS** | Ubuntu 22.04 / 24.04, or Debian 12 / 13 |
| **Architecture** | amd64 or arm64 |
| **Access** | root (or sudo) |
| **RAM** | 2 GB minimum; 4 GB+ recommended if you run several sites |
| **Disk** | 20 GB+ (WordPress images, databases, snapshots) |
| **State** | a **fresh** machine — the installer expects to own `/srv` and the nginx config |

A cloud VPS (Hetzner, OCI, DigitalOcean, Vultr, …) is ideal. ARM instances work great.

## Network

- **Ports 80 and 443** reachable from the internet (80 is required for Let's Encrypt
  validation and the HTTP→HTTPS redirect; 443 serves everything).
- When port-knocking is enabled, three random TCP ports in the `20000–60000` range
  must also reach the VPS. Their values are generated during installation.
!!! note "Cloud firewalls"
    If your provider has a cloud-level firewall (e.g. AWS Security Groups, OCI
    Security Lists or Hetzner Cloud Firewall), allow inbound TCP **80** and **443**.
    For port-knocking, add the three generated ports after installation and restrict
    them to trusted source IPs when practical.
    Do not expose Docker, database, Adminer or Mailpit container ports.

## Two hostnames

SpawnWP uses **two DNS names that you choose**, both pointing at the VPS:

- one for your **WordPress content** (e.g. `dev.example.com`)
- one for the **cockpit** and admin tools (e.g. `cockpit.example.com`)

They can be any names you control. See [DNS setup](dns-setup.md) for how to configure
them — and note that **both must resolve to the VPS before you install**, because the
installer obtains a TLS certificate for both during setup.

## Email

A contact email for Let's Encrypt (expiry notices). Any address you own.

---

Ready? → [DNS setup](dns-setup.md)
