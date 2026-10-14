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
