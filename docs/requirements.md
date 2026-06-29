---
description: Check supported Debian and Ubuntu releases, architectures, server resources, DNS and network requirements for SpawnWP.
---

# Requirements

You need a fresh server and two hostnames. The installer handles Docker, nginx,
certificates and the cockpit. It leaves the environment list empty; you create the
first WordPress environment after activating the cockpit.

You do **not** need to preinstall Docker, write nginx config, open custom admin
ports, or remember server commands for normal use. After setup, the cockpit is the
main interface.

## Server

| | |
|---|---|
| **OS** | Ubuntu 22.04 / 24.04 / 26.04, or Debian 12 / 13 |
| **Architecture** | amd64 or arm64 |
| **Access** | root (or sudo) |
| **RAM** | 2 GB minimum; 4 GB+ recommended if you run several sites |
| **Disk** | 20 GB+ (WordPress images, databases, snapshots) |
| **State** | a **fresh** machine — the installer expects to own `/srv` and the nginx config |

A cloud VM or VPS, a dedicated server, or bare-metal hardware all work. ARM servers
are supported as long as the operating system and architecture requirements above are met.

## Network

- **Ports 80 and 443** reachable from the internet (80 is required for Let's Encrypt
  validation and the HTTP→HTTPS redirect; 443 serves everything).
!!! note "Cloud firewalls"
    If your provider has a cloud-level firewall (e.g. AWS Security Groups, OCI
    Security Lists or Hetzner Cloud Firewall), allow inbound TCP **80** and **443**.
    Do not expose Docker, database, Adminer or Mailpit container ports.

## Two hostnames

SpawnWP uses **two DNS names that you choose**, both pointing at the server:

- one for your **WordPress content** (e.g. `dev.example.com`)
- one for the **cockpit** and admin tools (e.g. `cockpit.example.com`)

They can be any names you control. See [DNS setup](dns-setup.md) for how to configure
them — and note that **both must resolve to the server before you install**, because the
installer obtains a TLS certificate for both during setup.

## Email

A contact email for Let's Encrypt (expiry notices). Any address you own.

---

Ready? → [DNS setup](dns-setup.md)
