# HA Relapse 0.4.8

Local Relapse hosting and payload management for **Home Assistant OS and PS5 Relapse exploit**. Exploit files are included; upload your own payloads. HA needs internet access for installation, but the PS5 can operate with WAN blocked.

Throughout this guide, **`HA_IP` means your Home Assistant's local IPv4 address** and **`PS5_IP` means your PS5's local IPv4 address**. Replace these placeholders with your own addresses wherever shown.

## Simple setup

### 1. Find and reserve your addresses

1. Connect HA, the PS5 and your computer to the same home LAN, with device-to-device access allowed.
2. In HA, open **Settings → System → Network** and check the active adapter's IPv4 address. Alternatively, find HA in your router's **Connected devices / DHCP clients** list. Record this as `HA_IP`, without any `/24` suffix.
3. On PS5, open **Settings → Network → Connection Status → View Connection Status**. Record its IPv4 address as `PS5_IP` and the MAC address for the connection you use: **Wi-Fi** or **LAN cable**.
4. Open your router's app or admin page. Its address is usually listed as **Router / Default gateway** in your computer's network details. Find **DHCP reservation**, **Address reservation** or **Static lease**.
5. Reserve HA's current address for its active adapter's MAC, then do the same for the PS5. Selecting each device from the connected-device list is usually easiest. Save/apply, and follow any reconnect prompt.
6. Leave both devices' **IP assignment on Automatic / DHCP**. Check they receive the reserved addresses before continuing. The PS5's DNS will be changed separately below.

A reservation keeps the same address after restarts without manually configuring an IP on the device. Router menu names vary; use your model's address-reservation instructions. [Reservation example](https://service-provider.tp-link.com/uk/faq/439/)

### 2. Install HA Relapse

1. Open **Settings → Apps → Install app / App store** (**Add-ons** on older HA).
2. If needed, install **Samba share**, set its username and password, save, and start it.
3. Browse HA's Samba shares from your computer: `\\HA_IP` in Windows File Explorer or `smb://HA_IP` in Linux Files / macOS Finder. Replace `HA_IP` with your address and use your Samba credentials. [Samba instructions](https://www.home-assistant.io/common-tasks/os/#installing-and-using-the-samba-app)
4. Extract the ZIP and copy **relapse_local** into the **addons** share. Check the resulting path is `addons/relapse_local/config.yaml`.
5. In the app store, choose **⋮ → Check for updates / Reload**, refresh the page, then install **HA Relapse** under local apps.
6. Under **Configuration**, replace any prefilled `server_ip` with your `HA_IP` and save. Enter the address only, without `http://` or a port. Enable **Start on boot** and **Include in sidebar** (also called **Show in sidebar**).

### 3. Choose DNS blocking or full WAN isolation

**Changing HA to port 8123 is only needed for full WAN isolation if HA currently uses port 80.** For DNS-only blocking, keep HA's current port and Relapse's default HTTP mapping at **8088**, start HA Relapse, then continue to step 4.

HA Relapse's DNS blocks normal lookups for PlayStation servers; the Guide and connectivity-check names resolve locally instead. This only covers queries sent to HA. Cached addresses, direct-IP connections or another DNS resolver can bypass it. Choose the full WAN setup below if you want to prevent all PS5 internet access, including those routes, while keeping LAN access.

For internet access through nanoDNS instead, use the optional setup below. Keep your existing port mappings; a full WAN block would also block the internet access you want.

For full WAN isolation, give Relapse port **80**:

1. If HA uses port **80**, open **Settings → System → Network → HTTP server** and change **Server port** to **8123**. If it already uses 8123, leave it there.
2. Save and let HA restart. Open `http://HA_IP:8123` and **confirm within five minutes** if prompted. Update bookmarks/Companion app URLs. Keep `https://` if your HA uses HTTPS. [HA instructions](https://www.home-assistant.io/integrations/http/)
3. Stop **HA Relapse** if running. Under **Configuration → Network**, set these host ports and click **Save**:

| App entry | Host port |
| --- | --- |
| `1053/udp` | `53` |
| `1053/tcp` | `53` |
| `8080/tcp` | `80` |
| `8443/tcp` | `443` |

4. Start HA Relapse. On your computer, open `http://HA_IP/netstart/icst`. It should show **Success**.
5. To block internet access, create a rule in your router's **Firewall / Traffic rules** using the settings below, then save/apply it. Menu names vary by router.

| Rule setting | Value |
| --- | --- |
| Source device | Only your PS5, matched by its active MAC address |
| Direction | Home LAN → Internet / WAN |
| Protocol and ports | Any / All |
| IP families | IPv4 **and** IPv6 |
| Action | Reject |
| Local access | Allow PS5 ↔ HA; keep LAN access enabled |

If your router matches IPs instead, use your reserved `PS5_IP` for IPv4 and configure IPv6 blocking separately. An IPv4-only rule does not cover IPv6. A generic **Pause device** option may also block LAN access; check your router's documentation. The confirmed setup used **Reject**; **Drop** is not a verified substitute.

### Optional: internet through nanoDNS

Keep the PS5's DNS pointed at HA. Relapse stays local; nanoDNS filters other lookups once loaded.

1. Download **`nanodns.elf` (PS5)** from the [nanoDNS 0.4 release](https://github.com/drakmor/nanoDNS/releases/tag/0.4). **`nanodns-ps4.elf` is the PS4 build** and will not work on PS5; renaming it does not convert it. Payload Manager may display only the short name, so check the uploaded file.
2. Back up your existing INI. In the ZIP's `relapse_local/examples/nanodns.ini.example`, replace **`PS5_IP`** with the PS5's reserved address. Save it on the PS5 as **`/data/nanodns/nanodns.ini`**, using your file manager. `bind` is the PS5's address; it must not be HA's address.
3. In HA Relapse **Configuration**, set **nanoDNS PS5 IP** (`nanodns_ip`) to `PS5_IP`. Keep `server_ip` set to `HA_IP`. Save and restart HA Relapse. If the optional field is hidden, enable **Show unused optional configuration options** or use the configuration YAML editor to add `nanodns_ip: "PS5_IP"` with your actual address.
4. Keep the PS5's **Primary DNS = `HA_IP`**, **Secondary = `0.0.0.0`**. Its own LAN address stays unchanged.
5. Upload the PS5 ELF under **Additional payloads**. After Relapse and the primary payload have started, open **Payloads**, confirm **Primary payload started**, then load nanoDNS. Tick **Include in restore** in HA and place it before tools that need internet. Reload nanoDNS after each full PS5 shutdown; INI changes apply at its next startup.
6. Allow IPv4 internet access and LAN traffic between HA and the PS5. nanoDNS must be able to reach its upstream DNS servers on **UDP 53**. Keep any router-level IPv6 block you use; `bind6=off` only disables nanoDNS's IPv6 listener.

The example has no Sony exceptions. nanoDNS's stock exceptions allow some PlayStation domains, so replacing only `bind` in the stock file does not apply this policy. Keep its upstream servers external: **never point them back at HA**, which would create a forwarding loop. [nanoDNS configuration](https://github.com/drakmor/nanoDNS#config)

Before nanoDNS starts, other DNS lookups fail; HA does not fall back to another resolver. This does not disconnect existing connections or block direct IPs, cached addresses or DNS-over-HTTPS. **Filtered internet access is not complete Sony/WAN isolation.** Leave `nanodns_ip` empty and restart HA Relapse to return to offline DNS.

Verified on PS5 **13.60** with nanoDNS **0.4**: allowed lookups through HA over UDP/TCP, blocked Sony API/update samples, and local Guide/connectivity responses. Recomp Store and GitHub lookups succeeded. Repeated-boot reliability has not been tested.

### 4. Upload your payloads

1. Open **HA Relapse** from the sidebar or its **Open web UI** button.
2. In **Primary payload**, choose one compatible initial payload and **Upload**:

| Primary choice | Use |
| --- | --- |
| Standalone **kstuff / kstuff-lite** | Load kstuff, then additional tools. |
| **etaHEN** | Follow that build's instructions for included kstuff/features. |
| Another combined ELF | Must support your firmware and loader. |

3. In **Additional payloads**, upload tools such as **ShadowMountPlus** or an FTP payload.
4. Before loading them on the PS5, run Relapse and your primary payload. Wait for its startup notification or verify it is running. Open **Payloads**, then click **Primary payload started**. This unlocks additional payloads and **Restore payloads**.

Choose firmware-compatible releases without duplicate components. If your ShadowMountPlus release requires standalone kstuff without etaHEN, use that combination. Compatibility is your responsibility.

Upload extracted x86-64 ELF files, up to **64 MiB** each. `.bin` files must also contain ELF data. **Display name** is optional; filenames need no changes.

### 5. Choose what Restore loads

1. In HA's **Additional payloads**, tick **Include in restore**. Changes save automatically and survive shutdowns/restarts.
2. Set the order with **Move up / Move down**. **Wait after sending** sets the delay between files; default five seconds.

On the PS5, selected entries show **Restore** in green beneath their names. Press **Refresh list** after changing selections in HA. The label shows selection, not running status.

### 6. Launch on the PS5

1. Connect the PS5 to HA's LAN. Open **Settings → Network → Settings → Set Up Internet Connection → your connection → Advanced Settings**.
2. Keep **IP Address Settings = Automatic**. Set **DNS Settings = Manual**: **Primary = your `HA_IP`**, **Secondary = `0.0.0.0`**. Save; keep the local network connected. Apply this DNS change only to the PS5, not your router or HA.
3. Open **User's Guide** from PS5 Settings. Accept the local certificate prompt if shown, then select **Relapse**.
4. Wait for the exploit to finish. Select **Primary payload**.
5. Wait for its PS5 startup notification or verify it is running, then open **Payloads**.
6. Click **Primary payload started** to unlock additional payloads and **Restore payloads**. Select one entry to load it, or **Restore payloads** to load your selected entries in order.

Repeat after a full PS5 shutdown: app icons can remain while payloads need reloading. **Sent** confirms transfer, not successful startup.

### 7. Update or roll back

- **Payload update:** use **Replace file**, or upload the same original filename for an additional entry. Restore settings stay saved. No app rebuild/restart is needed.
- **Payload rollback:** select **Roll back** to restore its previous file. Reboot the PS5 and run the launch sequence before using the restored payload.
- **App update:** stop HA Relapse, replace `addons/relapse_local`, refresh the store, then **Update / Rebuild** and start. Keep your port settings. **Do not uninstall**: same-app updates preserve your files and selections.

## How it works

### Local DNS and HTTP

HA's DNS redirects User's Guide domains to its own LAN address. The initial HTTPS request reaches Relapse on **443**, then redirects to local HTTP. Pages, scripts, workers and payload files come from HA.

HA also resolves `ena.net.playstation.net` locally: `/netstart/icst` returns `Success`; `/networktest/*` returns `OK`. These connectivity replies require no Sony connection.

Those checks use HTTP port **80**. For full WAN isolation, Relapse must answer them locally, so free **80** by moving HA to another port, such as **8123**, if necessary. DNS-only setups can keep HA's existing port and Relapse on **8088**. Relapse reads its configured host port at startup.

By default, other names receive no external DNS resolution. With `nanodns_ip` set, HA sends those requests through a loopback-only UDP/TCP adapter to nanoDNS's UDP listener on the PS5. Only nanoDNS chooses an external resolver and applies its INI rules; HA keeps answering Guide and connectivity names locally. HA does not use its system resolver or another upstream if nanoDNS is unavailable. Its startup log and `/health` identify the configured mode, not whether nanoDNS is currently running.

The lookup path is **PS5 → HA Relapse → nanoDNS on PS5 → external DNS**. Guide/connectivity lookups stop at HA. In the example INI, matching Sony domains return `0.0.0.0`; other domains go to the listed upstream servers. nanoDNS's empty `[exceptions]` section prevents the stock Sony exceptions from bypassing those rules. An IPv4 DNS query can still request an IPv6 address, so disabling the IPv6 listener is not an IPv6 traffic block.

**DNS blocking is partial isolation, not a firewall**: it cannot stop connections that bypass its lookups or guarantee that a domain list covers every Sony service. [DNS filtering limitations](https://developers.cloudflare.com/learning-paths/cybersafe/concepts/what-is-dns-filtering/). The optional full WAN rule blocks traffic for both IPv4 and IPv6 while allowing LAN access. Successful 13.60 runs with **Any / Reject** were reported on 0.4.2; reliability across repeated attempts remains unverified.

### Exploit and payload flow

The exploit runs on the PS5. Once its ELF loader is ready, the page downloads your chosen file from HA and sends it to that loader. No FTP connection is needed.

The primary slot accepts compatible ELF payloads without checking their features or firmware support. Startup confirmation is manual because successful transfer cannot prove successful initialization.

Restore sends selected **additional** payloads in order and stops on a transfer error. **Stop after current payload** cancels the remaining queue. Delays are not readiness checks. Restore uses saved choices rather than scanning installed PS5 icons.

### Storage, rollback and repeat loads

Files, names, selections and one previous version per entry live in persistent `/data/payloads`. Include the app's data in HA backups. Removing an entry deletes both versions.

Per-console-IP history in `/data/sessions.json` guards against repeat sends in the same loader session. The PS5 reservation keeps that identity consistent. A fresh loader session resets history; reboot before switching primary payloads.

Rollback changes the file available for a future load. It cannot undo a running payload or recover a frozen console.

### Address changes

A DHCP reservation ties an address to one network adapter's MAC. Switching between Wi-Fi and Ethernet requires updating the reservation and PS5 firewall match for the new adapter. If HA's address changes, update `server_ip`, restart HA Relapse, and update the PS5's primary DNS and your saved URLs.

If using nanoDNS and the PS5's address changes, update HA's `nanodns_ip` and the PS5 INI's `bind`, then restart both services.

If your router has no reservation feature, a manually configured static address is an alternative: choose an unused address in the same subnet, exclude it from the router's DHCP pool, and supply the correct subnet mask, gateway and DNS. Follow your router's and [HA's network instructions](https://developers.home-assistant.io/docs/operating-system/network/). Reservations are the simpler setup described above.

### Quick checks

- **App missing from the store:** check `addons/relapse_local/config.yaml`, reload the store, then refresh the page.
- **App will not start:** check its log for an IP setting or port conflict. Ports 53, 80 and 443 must be available for the mappings above.
- **Check the running version:** open `http://HA_IP/health`—use `http://HA_IP:8088/health` if keeping the default HTTP mapping. This guide covers **0.4.8**. `dns_mode` is `offline` or `nanodns`.
- **nanoDNS has no internet:** check `/health` shows `dns_mode: nanodns` and your `PS5_IP`. Confirm you uploaded the PS5 ELF and started it after updating the INI. Its log at `/data/nanodns/nanodns.log` should show `listening on PS5_IP:53`. Allow IPv4 WAN access and HA → PS5 UDP 53. If HA logs a DNS loop, correct nanoDNS's upstream and restart nanoDNS and HA Relapse.
- **Restore unavailable:** open **Payloads** on the PS5 and confirm primary startup; select at least one additional payload in HA. Entries already attempted in this session are skipped.

## Credits

- **Relapse exploit:** [Sonic_Iso / soniciso1](https://github.com/soniciso1/relapse), Jordy, ntfargo, ufm42 and Dr. Yenyen, with the researchers and contributors credited in [Relapse-Exploit](https://github.com/ntfargo/Relapse-Exploit). HA Relapse packages their exploit work with a local host and payload interface.
- **Offline HTTP and connectivity approach:** [owendswang's PS5 WebKit Autoloader for ESP32](https://github.com/owendswang/ps5-webkit-autoloader-esp32). Its [local connectivity replies and HTTPS-to-HTTP handoff](https://github.com/owendswang/ps5-webkit-autoloader-esp32/blob/da9aaf0931b3fd9d1fd66c3910ec1cc7414482ce/esp32-arduino.ino) informed the fix. This reference is an ESP32 host, rather than an HA add-on.
- **Optional DNS payload and example rules:** [drakmor/nanoDNS](https://github.com/drakmor/nanoDNS). nanoDNS is uploaded separately and is not bundled.
