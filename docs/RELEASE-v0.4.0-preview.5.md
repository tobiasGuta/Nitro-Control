# Nitro Control v0.4.0-preview.5 — AN515-58 qualified Fedora preview

This source preview brings physical **Breathing**, **Neon**, **Wave**,
**Shifting**, and **Zoom** to the four-zone Acer Nitro AN515-58 keyboard. It
also includes seven phases of reliability work across the helpers, native
RGB, dashboard, deployment, tray, and release checks. The release decision
is **Conditional GO** for the reference configuration. [Qualification results](HARDWARE-QUALIFICATION-RESULTS.md)

The qualified source is `1d9f4274b76c8d05d7856a77b540ebd665ca7cb4`.
The existing `v0.4.0-preview.5` tag and its source archives identify an older
snapshot. The application version remains `0.4.0-preview.5`. The qualified
prerelease should use a distinct tag on the [GitHub Releases page](https://github.com/tobiasGuta/Nitro-Control/releases).
[Qualified source revision](https://github.com/tobiasGuta/Nitro-Control/tree/1d9f4274b76c8d05d7856a77b540ebd665ca7cb4)

## What changed

| Phase | Improvements |
| --- | --- |
| 1 | Fan helper operations use a verified private lock. Custom settings require explicit confirmation, and readback and Restore Auto report their results. |
| 2 | Native modules stage privately for the exact kernel, with provenance, interrupted-transaction recovery, and checks for legacy indexes and boot exposure. |
| 3 | Native RGB checks firmware responses, verifies Static and Dynamic state, enforces full-brightness initialization, and excludes diagnostic parameters from the supported build. |
| 4 | The optional RGB helper publishes a capability and digest manifest, verifies coherence before privileged use, and preserves locally modified artifacts. The dashboard checks capabilities and backend support before offering authorization. |
| 5 | Live and Demo activate independently. Refresh and write workers recover without leaving controls stuck. Stale observations expire, and graphs preserve missing data and sensor identity. NVIDIA polling targets the intended PCI device and avoids querying it when it is reported suspended. |
| 6 | The per-user installer preflights complete releases before atomic activation. Upgrade, rollback, uninstall, and optional autostart respect file ownership. The separate tray can open the current Live release after an upgrade. |
| 7 | Pinned CI runs Python 3.11–3.15 and required desktop, native, and isolated lifecycle checks. A portable Bubblewrap fixture preserves the first-use lock-inode test. |

**NC-Q01** restores executable permissions on five documented shell entry
points. **NC-Q02** verifies the installed RGB helper payload and confirms the
transaction marker is gone before reporting success. Recovery from the
interrupted installation passed on the physical reference laptop.
[Correction record](NC-Q02.md)

## Installation on Fedora

Use the qualified source revision above or the source archive for its approved
publication tag. Close the dashboard and quit any running tray before upgrading.
Install the GUI dependencies and stage the app as your regular user.

```bash
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita
./scripts/install-fedora.sh
~/.local/bin/nitro-control --version
```

The version command should print `Nitro Control 0.4.0-preview.5`. Run
`~/.local/bin/nitro-control` for Live or append `--demo` for simulated data.
The tray and its login autostart are optional, with login autostart off by
default. [GNOME tray setup](TRAY-INDICATOR.md)

Physical fan and native RGB controls are separate opt-ins. Use the matching
optional fan helper for supported stock-driver fan control. For physical native
RGB, stage the default module built for the running kernel and install the
matching RGB helper. Follow the prerequisites for stock `acer_wmi`, kernel
headers, module signing, and any inspected legacy migration. Preserve Secure
Boot policy and leave module autoload disabled. [Fan setup](FAN-CONTROL.md#install-and-open-the-dashboard) · [Native RGB setup](KEYBOARD-RGB.md#install-physical-effects-on-fedora) · [Native staging and recovery](NATIVE-RGB.md)

## Safety and rollback

- In **Keyboard RGB → Dynamic**, choose an effect and speed, then select
  **Apply effect to keyboard**. Breathing, Shifting, and Zoom use the selected
  color. The Dynamic preview brightness slider affects the preview drawing.
- Preview edits are in memory. Physical fan or RGB changes require an explicit
  request and administrator authorization through the optional helpers. The
  default RGB policy is `auth_admin`. Desktop power changes use the offered
  profiles from the system service.
- Custom or Max fan mode can persist after closing the app. Apply **Restore
  Auto** and verify both channels before ending a supervised session. If a
  request times out, wait for it to settle and verify the hardware and module
  state before issuing another request.
- Set the Static editor to **100%** before a native Apply or Restore. Physical
  Dynamic effects initialize at **100%**, can continue after the app closes,
  and can be stopped by applying a known Static palette at 100%. The firmware
  brightness hotkeys are separate from the editor slider.
- For a GUI-only rollback, wait for outstanding helper operations to complete,
  close the dashboard and tray, then run
  `./scripts/install-fedora.sh --source /path/to/reviewed-preview.4` from the
  qualified checkout. Verify `~/.local/bin/nitro-control --version` and relaunch.
  The GUI rollback preserves preferences and leaves separately installed helpers
  and module staging in place. An older GUI can still send supported Static
  requests to the current RGB helper. [Reviewed preview.4 source](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.4) · [Deployment rollback](PHASE-6.md#supported-commands)
- To remove the GUI, use `./scripts/uninstall.sh`. To roll back optional
  system components, first restore Auto and the intended power and lighting
  state while the controls are available. Use each component's documented
  removal or recovery procedure after outstanding requests finish.
  [Fan recovery](FAN-CONTROL.md#upgrading-or-rolling-back-the-fan-helper) · [Native RGB recovery](NATIVE-RGB.md)

## Where qualification stops

The physical checks cover one Acer Nitro AN515-58 with BIOS V2.21, Fedora 45,
kernel `7.3.4-300.fc45.x86_64`, GNOME Wayland, and AppIndicator 66. The
operator verified all five effects at speed 4, return to Static, both brightness
hotkeys, and two deep suspend/resume cycles. The operator visually confirmed
effect color and animation. Historical low-brightness behavior remains
unresolved. Other models, kernels, effect speeds, and sustained heavy thermal
workloads remain unqualified.

NVIDIA Runtime D3 was disabled by default with driver `620.70.06`, so runtime
power qualification is deferred. The physical session exercised PolicyKit
cancellation and installed launcher Live and Demo modes. Separate denial,
next-login autostart, and GNOME app-grid activation of the newly staged install
remain outside the physical result. USB reinitialization, Wi-Fi RF-kill, GNOME
focus activation, and historical Ayatana warnings are recorded in the
[qualification report](HARDWARE-QUALIFICATION-RESULTS.md#deferred-checks-and-observations).
