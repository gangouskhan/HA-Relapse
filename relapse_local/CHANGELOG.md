# 0.4.11

- Public release includes the Klog capture/history added in 0.4.9 and automatic scrolling added in 0.4.10. Connect remembers the last IP and port across app restarts.
- Fix selecting/copying Klog text expanding or resetting during refresh. Keep the log's text node, append new output and skip unchanged text.
- Pause display changes while text is being selected or remains highlighted; collection on HA continues. Clear the selection to show buffered output.
- Stop polling a finished recording once all its bytes have been read. Automatic following and scroll-to-pause behavior are retained.
- Reorganize the README into core features, instructions and how it works, with Klog setup and troubleshooting.

# 0.4.10

- Remove the Klog Follow checkbox. Live output follows automatically, pauses when you scroll up, and resumes when you return to the bottom.
- Preserve a paused reading position when switching tabs. Capture on HA continues while the viewer is paused.
- Klog capture and history in 0.4.9 were confirmed working by the user; this update changes the viewer only.

# 0.4.9

- Add a Klog tab to the HA upload interface: connect to the PS5's klog server, view live output, stop capture and browse previous recordings.
- Record on HA while connected, including when the browser is closed. View, download or delete saved recordings; downloads include all bytes received before the download started.
- Preserve recordings across app restarts, mark interrupted captures and retain data after connection failures. No automatic reconnect or log deletion.
- Limit recordings to 64 MiB each, 512 MiB total and 200 entries. Capture stops at the storage limit; delete older recordings to continue. The browser keeps a bounded text preview.
- Klog APIs are available only through HA ingress. No new published port, exploit modification or bundled klog payload. Local fake-server regression and browser checks cover the new workflow; live PS5 verification is pending.

# 0.4.8

- Optional `nanodns_ip`: keep PS5 DNS pointed at HA while forwarding other DNS requests to nanoDNS on the PS5. User's Guide and connectivity responses stay local, including before nanoDNS starts.
- No fallback to HA's, the router's or a public DNS resolver. Detect forwarding loops; reject HA's own address as the nanoDNS destination. Leaving the option empty preserves offline DNS.
- Add nanoDNS setup instructions, including the correct PS5 ELF, its LAN listener, empty Sony exceptions and the difference between filtering and full WAN isolation. Payloads remain user supplied.
- Local DNS integration tests cover forwarding, local redirects, upstream failure/recovery and offline updates. Verified on PS5 13.60 with nanoDNS 0.4: allowed HA UDP/TCP lookups, blocked Sony API/update samples and local Guide/connectivity responses. Repeated-boot reliability is untested.

# 0.4.7

- Fix the PS5 payload screen showing only ShadowMountPlus even though the count included other payloads and Restore could load them. Missing green Restore labels and transfer status are also fixed. Confirmed on the affected PS5 running 13.60.
- Lay out the additional panel and its text before Relapse, following the upstream prebuilt-menu approach, and preserve unchanged rows on Refresh.
- Show the payload count and keep compact display diagnostics in the local HA log, without filenames or payload contents.
- Correct the launch instructions: load the primary payload, wait for startup, open **Payloads**, then click **Primary payload started**.

# 0.4.4

- Show Restore selections as clear green `Restore` text beneath payload names on the PS5.

# 0.4.3

- Show a green `✓ Restore` badge on each PS5 payload selected for Restore in HA.
- Keep badges readable while payload buttons are disabled or already sent.
- Refresh list updates the badges from the saved HA selections.

# 0.4.2

- Answer PS5 connectivity requests locally: `ena.net.playstation.net`,
  `/netstart/icst` and `/networktest/*`.
- Hand the HTTPS User's Guide entry to local HTTP. Exploit assets, workers,
  progress beacons and payload requests then use the same HTTP origin.
- Read the configured HTTP host port from Supervisor, with a clear startup
  message when port 80 still needs configuring. Keep the default at 8088 to
  avoid taking the port currently used by Home Assistant.
- Renew the local certificate once to include the connectivity hostname.
- Preserve the existing exploit code, user library and rollback data.

# 0.4.1

- Fix uploads rejected with “Use the HA Relapse interface for this request” when
  Home Assistant Ingress streams POST requests using chunked transfer encoding.
- Support streamed uploads and management actions while retaining access checks,
  size limits and protection against incomplete or malformed transfers.
- Add regression tests and run browser workflows through a local streaming proxy.

# 0.4.0

- Empty user library: etaHEN, kstuff and ShadowMount are no longer bundled.
- HA upload UI with primary and additional tabs.
- Persistent files, original filenames, optional labels and one-version rollback.
- Two-button PS5 menu with primary startup confirmation and a dynamic payload list.
- Selected, ordered restore with configurable waits and stop control.
- Durable send-attempt guards and bounded, revision-specific transfers.
- WebKit/kernel algorithms and required loader binaries unchanged.

Upload your own payloads after upgrading from 0.3.0. See DOCS.md for installation
and testing limits.
