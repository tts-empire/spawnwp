# Requirements

You need a fresh server and two hostnames. The installer handles everything else:
Docker, nginx, certificates, the cockpit, optional port-knocking and the first WordPress
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
- With the recommended port-knocking option enabled, **three high TCP ports** generated
  during installation must also reach `knockd` for the cockpit's secret knock sequence.
  They do not expose applications; Adminer, Mailpit and the cockpit itself are still
  served only over HTTPS on port 443.

!!! note "Cloud firewalls"
    If your provider has a cloud-level firewall (e.g. AWS Security Groups, OCI
    Security Lists, Hetzner Cloud Firewall), allow **80**, **443**, and the three TCP
    knock ports printed in `/root/spawnwp-credentials.txt`. Restrict the knock-port
    rules to your own source IP when possible. If you explicitly disable port-knocking
    during installation, only 80 and 443 are required.

## Two hostnames

spawnwp uses **two DNS names that you choose**, both pointing at the VPS:

- one for your **WordPress content** (e.g. `dev.example.com`)
- one for the **cockpit** and admin tools (e.g. `cockpit.example.com`)

They can be any names you control. See [DNS setup](dns-setup.md) for how to configure
them — and note that **both must resolve to the VPS before you install**, because the
installer obtains a TLS certificate for both during setup.

## Email

A contact email for Let's Encrypt (expiry notices). Any address you own.

---

Ready? → [DNS setup](dns-setup.md)
