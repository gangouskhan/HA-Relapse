# Porting HA Relapse to Proxmox VE

**Status: proposed conversion, not an installation guide.** Based on HA Relapse **0.4.8**, reviewed **25 October 2026**.

**This port has not been built or tested on Proxmox.** No Proxmox test environment was available when writing this document. The current HA release ZIP is not a standalone Proxmox package.

## Proposed approach

Run the host as a native Linux service inside a **Debian LXC container with its own LAN IP**. This avoids needing a Home Assistant OS VM. LXC containers share the Proxmox host's Linux kernel; they are not full virtual machines. Start with an unprivileged container and validate the service permissions there. [Proxmox container documentation](https://github.com/proxmox/pve-docs/blob/master/pct.adoc)

Most of the existing application can be reused: Relapse pages, local DNS/connectivity replies, payload uploads, rollback, Restore and optional nanoDNS forwarding. The exploit and uploaded ELFs still execute on the PS5. Moving the host does not add PS5 firmware compatibility.

The main work is replacing HA startup/configuration and dashboard authentication. Docker inside LXC, USB passthrough and installation on the Proxmox node itself are unnecessary for this proposed design.

## Conversion steps

These are tasks for a contributor implementing the port, not steps that make the current ZIP work unchanged.

### 1. Prepare the container and address

- Create a Debian container from a template supported by the installed Proxmox version. Attach its network interface to the existing LAN bridge, commonly `vmbr0`. Use the bridge/VLAN that lets the PS5 reach the container. [Proxmox networking](https://github.com/proxmox/pve-docs/blob/master/pve-network.adoc)
- Give it a persistent address: reserve its network interface's MAC in the router and use DHCP, or configure an unused static address with the correct prefix and gateway, outside conflicting DHCP allocations.
- Call this address **`RELAPSE_IP`**. It is the container's address, not the Proxmox management address or the PS5's address. Call the console's reserved address **`PS5_IP`**.
- Keep the container's own OS DNS pointed at a normal working resolver for package installation and updates. Do not make Relapse's offline DNS the household resolver.

No CPU, RAM or disk minimum has been validated. Allow storage for uploaded payloads and their previous versions; uploads can be up to 64 MiB and are buffered in memory.

### 2. Replace the HA packaging

Install Python 3, OpenSSL and the `dnsmasq` executable inside the container. Debian's **`dnsmasq-base`** package supplies that executable without installing a separate system daemon; HA Relapse starts its own DNS child process. Check no other service owns the selected ports. [Debian package description](https://packages.debian.org/trixie/dnsmasq-base)

Preserve the layout of [`relapse_local`](https://github.com/gangouskhan/HA-Relapse/tree/v0.4.8/relapse_local), including the Python modules, `admin`, `www`, `UPSTREAM.json` and `PAYLOAD_MENU.json`. Keep the pinned exploit assets, checksums and licences intact.

Replace the HA-specific `run.sh`, `bashio` and Supervisor port lookup with a normal service entrypoint. A systemd unit should start the Python host, restart it after failure and retain its logs. The current host already monitors its DNS process, HTTP threads and optional nanoDNS relay.

### 3. Provide standalone configuration

[`host.py`](https://github.com/gangouskhan/HA-Relapse/blob/v0.4.8/relapse_local/host.py) already accepts the following arguments:

| Setting | Proposed standalone value |
| --- | --- |
| `--server-ip` | `RELAPSE_IP` |
| `--nanodns-ip` | Optional `PS5_IP`; omit to keep other DNS names offline |
| `--data-dir` | A persistent application directory, for example `/var/lib/ha-relapse` |
| `--dns-port` | `53` |
| `--http-port` | `80` |
| `--public-http-port` | `80` |
| `--https-port` | `443` |

Passing `--server-ip` bypasses the HA `options.json` lookup. Document these arguments in the service configuration; arbitrary environment variables are not currently read as application settings.

The HA defaults use internal ports **1053/8080/8443** with published mappings. A native LXC service has no automatic translation: configure its actual listeners and advertised HTTP port to match. The service account needs permission to bind low ports inside the container.

### 4. Replace HA Ingress authentication

This is required before exposing the upload dashboard. Its current access policy trusts HA's Ingress proxy, not standalone user logins. Simply opening port **8099** will not provide a usable standalone dashboard.

Implement either a standalone login or an authenticated reverse proxy. For a proxy design, add a separate loopback-only administration listener and adjust the trusted-peer policy accordingly. The current `--listen` applies to public and admin listeners together; its test-only loopback override cannot expose the PS5 pages while keeping only administration on loopback.

Keep request-header checks, upload validation and size limits. Prevent direct access to the proxy's backend, and preserve binary/chunked uploads. Protect the management login with HTTPS; leave the PS5's existing local HTTP exploit flow intact. See [`http_api.py`](https://github.com/gangouskhan/HA-Relapse/blob/v0.4.8/relapse_local/http_api.py).

### 5. Preserve data and backups

Keep the configured data directory across application updates. It contains payload files and metadata, previous versions, Restore choices, session history and generated certificates. Store login configuration/secrets persistently too if the port adds them.

Keeping data on the container's root disk is the simplest proposed arrangement. If using another Proxmox-managed volume, enable its backup option. **Host bind mounts and device mounts are not included in Proxmox container backups** and need separate backups. Verify a restore before relying on it. [Proxmox backup documentation](https://github.com/proxmox/pve-docs/blob/master/vzdump.adoc)

Enable container startup on boot and enable the application service inside it. Test both restart paths. Updating code must not replace the data directory.

## Network behaviour to preserve

| Destination on `RELAPSE_IP` | Purpose |
| --- | --- |
| 53 UDP and TCP | PS5 DNS |
| 80 TCP | Local pages and connectivity replies |
| 443 TCP | HTTPS User's Guide entry |
| Separate authenticated management endpoint | Uploads and settings; keep its backend private |

With a dedicated container IP, HA or another container can keep its own ports **80/443**. No Home Assistant port change is needed. Allow the required traffic through any router, VLAN, Proxmox or container firewall that applies; do not expose these services through internet port forwarding.

Keep the existing local DNS rules for `support.playstation.com`, `manuals.playstation.net`, `www.playstation.com`, `relapse.home.arpa` and `ena.net.playstation.net`, including their subdomain handling. Preserve the HTTPS-to-local-HTTP handoff. On port **80**, `/netstart/icst` must return **`Success`** and `/networktest/*` must return **`OK`** for accepted paths. These are local responses, not requests forwarded to Sony.

By default, other names stay offline. This DNS policy is **partial isolation**, not a firewall: cached addresses, direct IPs and alternative resolvers can bypass it. Complete isolation still needs a PS5-specific WAN rule covering **IPv4 and IPv6**, while allowing LAN access. A firewall protecting the LXC alone does not block the PS5's separate route to the internet.

## Optional nanoDNS

Retain the 0.4.8 forwarding path:

**PS5 → Relapse container → nanoDNS on PS5 → external DNS**

Guide/connectivity names stop at the container. Set `--nanodns-ip` to `PS5_IP`, keep the PS5's primary DNS at `RELAPSE_IP`, and configure nanoDNS's `bind` as `PS5_IP`. Use the PS5 ELF; do not use the PS4 build. Its upstream servers must not point back at Relapse.

Allow the container to reach PS5 UDP **53**, and nanoDNS to reach its external resolvers. Until nanoDNS starts, ordinary external lookups fail; there is no fallback resolver. A full PS5 WAN block also prevents the intended internet access. Keep any IPv6 traffic policy separate from nanoDNS's listener settings.

Use the [existing nanoDNS setup instructions](https://github.com/gangouskhan/HA-Relapse/blob/v0.4.8/README.md#optional-internet-through-nanodns), replacing `HA_IP` with `RELAPSE_IP`. The Proxmox arrangement itself remains untested.

## PS5 flow to keep

1. Upload one compatible **Primary payload** and any **Additional payloads** through the authenticated dashboard. Keep **Include in restore** selections and ordering available there.
2. Set only the PS5's DNS: **Primary = `RELAPSE_IP`**, **Secondary = `0.0.0.0`**. Its own LAN address remains unchanged.
3. Open **User's Guide → Relapse → Primary payload**. Wait for the primary payload to start.
4. Open **Payloads**, then select **Primary payload started**. Load an entry or use **Restore payloads**.

Preserve the green **Restore** labels and session protections. **Sent** means transfer succeeded, not that the payload initialized. Restore choices survive reboots; running PS5 payloads generally need reloading after full shutdown.

## Validation before claiming support

- Confirm startup, clean shutdown, recovery and low-port binding inside an unprivileged LXC; record any permissions needed.
- From another LAN device, test DNS over UDP/TCP, local domain answers, HTTP connectivity replies, HTTPS handoff and unchanged exploit assets.
- Verify dashboard login, rejected unauthenticated/direct-backend access, uploads, replacement, rollback, Restore order and data persistence.
- Run the full PS5 sequence repeatedly, including the intended WAN-blocked arrangement. If offering nanoDNS, test allowed/blocked lookups and behaviour before nanoDNS starts or after it stops.
- Test application/container restarts, Proxmox reboot, backup restoration and updates without disrupting other guests.

The [existing tests](https://github.com/gangouskhan/HA-Relapse/tree/v0.4.8/tests) are a starting point, not proof of Proxmox compatibility. Reports should include Proxmox and Debian versions, application revision, container/network configuration, PS5 firmware and observed results.

## Credits

Retain the project's [upstream credits and licences](https://github.com/gangouskhan/HA-Relapse/blob/v0.4.8/README.md#credits): the [Relapse exploit](https://github.com/soniciso1/relapse) and its contributors, [owendswang's ESP32 offline host](https://github.com/owendswang/ps5-webkit-autoloader-esp32) for the connectivity replies/HTTP handoff approach, and [drakmor/nanoDNS](https://github.com/drakmor/nanoDNS) for optional DNS filtering.
