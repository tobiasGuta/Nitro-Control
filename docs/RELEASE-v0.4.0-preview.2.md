# Nitro Control v0.4.0-preview.2 — NitroSense-inspired GUI

> **Release candidate notes — NOT YET PUBLISHED.**
> This document describes a proposed source-based GitHub preview release.
> It is not proof that a `v0.4.0-preview.2` tag, published Release page,
> Fedora RPM, Flatpak, AppImage or installable binary exists. Publish only
> after the gates in [issue #6](https://github.com/tobiasGuta/Nitro-Control/issues/6)
> are complete.

## Highlights

This update brings the owner's screenshot-tested **NitroSense-inspired**
look into an **original** Nitro Control GTK4/Libadwaita application:
graphite panels, chamfered steel headers, crimson accents, a dashboard
that fills a maximized screen, and matching Hardware Details and
Keyboard RGB pages. The actual feature set remains limited to supported
Linux readings and guarded controls.

- Two **animated turbine dials** show the actual `acer` HWMON fan RPM.
  On the tested AN515-58, upstream `acer_wmi` channel order identifies
  **fan1 = CPU Fan** and **fan2 = GPU Fan**. Other unverified models keep
  generic labels. Animation is visual-only and intentionally slowed
  relative to physical rotation; it does not read or write fan PWM.
- **Real sensor history** for CPU package temperature, NVIDIA GPU
  temperature and GPU utilization (when reported). Missing data remains
  unavailable rather than being fabricated. Read-only NVIDIA details,
  NVMe and unidentified Acer temperature sensors remain accessible.
- **Power Mode** shows only modes offered by the Linux desktop
  power-profile service. Changes still require the original confirmation
  and service authorization; Acer firmware/TuneD readings are informational.
- **Four-zone keyboard RGB** now has a clearly labeled, local-only
  draft-palette strip and matching color editor. Loading a preset,
  applying a simulated preview and resetting the draft do **not** write
  the keyboard. The opt-in native WMI physical Apply/Restore backend is
  unchanged and requires editor brightness **exactly 100%**, explicit
  confirmation and administrator authorization.
- **Unsupported capabilities are not offered:** no manual fan PWM/Max/
  Custom/CoolBoost, no EC poking, no replacing stock `acer_wmi`, no
  unattended boot-time or resume-time firmware writes.

**Source change:** [PR #5](https://github.com/tobiasGuta/Nitro-Control/pull/5).
**Release planning and checks:** [issue #6](https://github.com/tobiasGuta/Nitro-Control/issues/6).
**Detailed design validation:** [NITROSENSE-UI-REDESIGN.md](NITROSENSE-UI-REDESIGN.md).

## Requirements and support limits

- **Fedora 45** reference environment; Python **3.11+**, GTK4,
  Libadwaita, PyGObject and **PyCairo**.
- User-space application install only; **no root privileges needed to
  install the GUI**. Native RGB physical operations remain separately
  opt-in and require an existing supported, kernel-pinned experimental
  helper/module and Polkit authorization.
- Verified primarily on **one Acer Nitro AN515-58**. Other laptop
  models, kernels, fan-channel assignments and firmware implementations
  have not been comprehensively tested.
- NVIDIA monitoring depends on the relevant NVIDIA driver/`nvidia-smi`
  being available. Without it, optional GPU telemetry is unavailable.
- The underlying native RGB controller can lose its zone state after
  some boot/update conditions; the exact trigger of the documented
  November incident remains **unproven**. See [issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).
  Initialization at 65% could cause unwanted Fn brightness/color behavior;
  see [issue #2](https://github.com/tobiasGuta/Nitro-Control/issues/2).

## Source installation on Fedora

**These commands are for the release candidate branch. Do not use a
nonexistent tag before a GitHub Release is actually published.**

To inspect the release candidate **without replacing the installed app**:

```bash
sudo dnf install python3-gobject python3-cairo gtk4 libadwaita
git clone https://github.com/tobiasGuta/Nitro-Control.git
cd Nitro-Control
git switch release/v0.4.0-preview.2
/usr/bin/python3 -m nitro_control --demo
```

`--demo` labels every reading as sample data and disables physical
hardware changes. For **live read-only monitoring**, omit `--demo`.
The UI update does **not** require installing any kernel module.

To **install or update the per-user GUI** from a fully reviewed
source/tag, explicitly run from that checkout:

```bash
bash scripts/install-fedora.sh
~/.local/bin/nitro-control --version
~/.local/bin/nitro-control --rgb-probe
~/.local/bin/nitro-control
```

The install script replaces only the per-user Python app package,
launcher and desktop entry. It changes **no kernel driver**, Polkit rule,
native RGB root-owned module, firmware, fan behavior or boot service.
Do not run a kernel-module installer as part of ordinary GUI upgrading.

After publication, a release tag can be explicitly checked out from
an independent clone rather than following an advancing development
branch; verify that the tag points at the reviewed release commit.

## Rollback

For a **GUI-only rollback** to the pre-redesign `main` state
(commit `a6496d50dd39278989c56239a21f97555a643694`),
use a **separate clean checkout/worktree**. From an existing Git checkout:

```bash
git fetch origin main
git worktree add --detach "$HOME/Nitro-Control-previous-gui" \
  a6496d50dd39278989c56239a21f97555a643694
bash "$HOME/Nitro-Control-previous-gui/scripts/install-fedora.sh"
~/.local/bin/nitro-control --version
```

This rolls back the **per-user GUI only**. It deliberately does not
uninstall the separately managed native RGB helper or kernel module,
touch `acer_wmi`, remove a legacy module, or change fan/EC behavior.
Avoid overwriting a dirty experimental development checkout.

## Release-candidate Fedora visual smoke test — November 11, 2026

The owner launched the proposed `0.4.0-preview.2` release candidate on
the reference **Acer Nitro AN515-58, Fedora 45**, and shared real GTK
screenshots of **all three pages** on November 11, 2026. The Dashboard
footer visibly reports `Nitro Control v0.4.0-preview.2`.

**Observed in the release-candidate screenshots:**

- **Dashboard:** CPU Fan **2,702 RPM**, GPU Fan **3,191 RPM**,
  CPU package **61 °C**, NVIDIA GPU **48 °C**, actual rolling CPU/GPU
  graphs, GNOME Balanced selected/active, Acer firmware and TuneD
  both balanced. Control mode is honestly **Mode not reported**.
- **Hardware Details:** Original metal/graphite panels render; optional
  NVIDIA metrics, firmware/TuneD profiles, real CPU/GPU fan readouts,
  and the temperature-sensor section are present. This screenshot
  captured GPU utilization **55%**; the two fan readouts are from a
  separate refresh and need not match the Dashboard's earlier RPM.
- **Keyboard RGB:** WMI interface **Detected**, no permanently bound
  driver, independent kernel-pinned native helper **present**; four
  draft-only colored tiles, preset selector, zone editors and brightness
  slider are visible. The full lower editor controls were tested during
  the preceding redesign development, but **not newly evidenced** by
  this release-candidate screenshot.

**Result:** PASS for launching and visually rendering the release
candidate, identifying the proposed version, and displaying live
sensor data and all three page designs. These screenshots **do not
verify** a new GUI install/rollback, the updated release candidate's
preset/preview/reset callbacks, power-profile confirmation/Cancel,
small-window scrolling, exact animation smoothness, or physical
keyboard Apply/Restore. The existing physical WMI controller was **not
reinitialized** merely for this cosmetic release review.

CI for the release-candidate branch passed Python **3.11** and
**3.13** tests. Publication still requires the remaining gates in
[issue #6](https://github.com/tobiasGuta/Nitro-Control/issues/6).

## Field validation and outstanding gates

The reference AN515-58 owner supplied screenshots of all three pages,
normal and maximized Dashboard layouts, fan names, real read-only
temperature/RPM graphs, and small-window RGB scrolling. They reported
passing:

- Preset selection → **Load preset** updates all four colors and the
  draft strip.
- **Apply preview** updates simulation while the physical LEDs stay
  unchanged.
- **Reset** restores the expected local editor state.
- Power-profile change confirmation **Cancel** workflow.
- Physical Apply/Restore buttons were visually **disabled at 65%** native
  editor brightness; this is the correct fail-closed UI state.

The **redesigned UI was not used to reinitialize the physically working
keyboard**, because that would unnecessarily issue a firmware operation.
The privileged native RGB backend and its safety gates have not changed.
Post-cleanup reboot/suspend-resume persistence was previously verified,
but **not a release-candidate-specific regression run**. The release
candidate's **three-tab visual/live telemetry smoke test** and CI are
now complete; remaining interaction, installation/rollback and provenance
verification are required before publication.

Source CI validates Python syntax, shell syntax and offline test suites
on Python **3.11 and 3.13**. This is not a substitute for real-hardware
verification on a target laptop.

## Changelog since v0.4.0-preview.1

- Original NitroSense-inspired visual redesign across all three pages.
- New real-only CPU/GPU time series and independent fan animations.
- Clarified AN515-58 CPU/GPU fan mapping and absent control-mode status.
- Corrected small/maximized layout proportions and vertical expansion.
- Four-zone draft-only RGB palette plus native brightness guard explanation.
- New PyCairo dependency check and regression tests.
- **No changes** to native RGB kernel module, Polkit authorization, fan
  controller, or automatic startup behavior.

**Distribution scope for first publication:** A GitHub Release tag with
source archives generated by GitHub. Do **not** advertise additional
binary packages or native RGB compatibility beyond tested conditions.
