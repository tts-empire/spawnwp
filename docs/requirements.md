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
| **Disk** | 20 GB+ (WordPress images, databases, snapshots). Steady state is roughly 2 GB per PHP version in use plus 100–250 MB per site |
| **State** | a **fresh** machine — the installer expects to own `/srv` and the nginx config |

A cloud VM or VPS, a dedicated server, or bare-metal hardware all work. ARM servers
are supported as long as the operating system and architecture requirements above are met.

SpawnWP keeps host memory safe by budgeting the Docker limits of running SpawnWP containers. It
reserves 20% of physical RAM for the operating system (at least 512 MiB, at most 1 GiB); a stopped
site uses no container budget. A default running site reserves about 1.1 GiB, so a 2 GB server is
appropriate for one running site at a time. You may keep additional sites Down and start them when
needed. Cockpit shows the current committed and available capacity on Manage and Deploy.

The default RAM admission policy is `enforce`: a create, start or increase to a running site's PHP
memory is refused when the committed container limits exceed the host budget. On a single-tenant
development or test host where the operator explicitly accepts swap and OOM risk, a root operator
may opt into advisory admission by adding this line to `/etc/spawnwp/config.env`:

```ini
SPAWNWP_RAM_ADMISSION=advisory
```

Advisory mode permits those operations and marks the host as overcommitted in Cockpit, but it does
not remove Docker cgroup limits and does not make swap part of the capacity guarantee. Keep the
default on shared or production hosts. An absent or invalid setting never enables advisory mode.

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
