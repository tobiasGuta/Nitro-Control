# Nitro Control v0.4.0-preview.5 hardware qualification results

**Decision — Conditional GO for a Fedora source preview on the reference configuration.** The qualified implementation is commit `1d9f4274b76c8d05d7856a77b540ebd665ca7cb4` on `release/0.4.0-qualification`. This report records the team's independent engineering acceptance and the operator's physical qualification summary against the [manual checklist](HARDWARE-QUALIFICATION.md).

## Reference environment

| Component | Tested configuration |
| --- | --- |
| Laptop and keyboard | Acer Nitro AN515-58 with a four-zone RGB keyboard |
| Firmware | BIOS V2.21 |
| OS and kernel | Fedora 45, `7.3.4-300.fc45.x86_64` |
| Platform driver | Stock `acer_wmi` |
| Discrete GPU | NVIDIA RTX 3050 Ti Laptop GPU |
| NVIDIA driver | `620.70.06` |
| Desktop | GNOME Wayland with AppIndicator extension version 66 |

## Engineering acceptance and artifact verification

- All seven engineering phases were independently accepted. All seven hosted jobs passed at the qualified commit, including Python 3.11 through 3.15, desktop-and-native, and lifecycle-integration. [Hosted qualification run](https://github.com/tobiasGuta/Nitro-Control/actions/runs/45698419664)
- **NC-Q01** corrected executable permissions on documented shell entry points, including direct native RGB uninstallation. **NC-Q02** corrected RGB helper transaction finalization. Recovery of the interrupted helper installation succeeded on the physical reference system. [Correction record](NC-Q02.md)
- The operator reported that all eight installed helper files checked matched the candidate. The source tree's RGB helper manifest defines six Python modules, the generated launcher, and the PolicyKit action as its eight payload entries.
- The installed native module matched the qualified build's recorded SHA-256 `1a03430613185ca7f9e39383bc8167bd335dc33e7c8f41efd474b6ff0d8d8c6d`.

## Verified results

| Area | Result |
| --- | --- |
| Discovery and telemetry | Hardware discovery and sensor reporting passed. Natural firmware-controlled fan ramp was observed in Auto. |
| PolicyKit and fan control | PolicyKit cancellation passed. Independent CPU and GPU Custom control, dual-channel Custom control, and verified restoration to Auto passed. |
| Power profiles | Switching the desktop power profile and restoring the original profile passed. |
| Static RGB and brightness | Four-zone Static lighting, the 100% initialization safeguard, and firmware brightness controls Fn+F9 and Fn+F10 passed. |
| Dynamic RGB and restoration | Breathing, Neon, Wave, Shifting, and Zoom passed at speed 4. Dynamic-to-Static restoration passed. The operator visually confirmed effect color and animation. |
| Module and driver lifecycle | The one-shot native module cleaned up after use and coexisted with stock `acer_wmi`. |
| Suspend and resume | Two deep suspend/resume cycles passed, including visual recovery and recovery in the application. |
| GNOME tray | All 11 live workflow checks passed on the reference GNOME Wayland session. |
| Isolated deployment | All 12 workflow checks passed, including Live and Demo via the installed launcher. These checks were isolated from the main user installation. |

## Deferred checks and observations

- **NVIDIA runtime power.** Qualification is deferred because the baseline driver reports Runtime D3 disabled by default. There is no confirmed Nitro Control defect associated with this result.
- **Login autostart and desktop entry activation.** Optional tray launch at login was outside the physical test. Installed launcher Live and Demo modes passed. Actual activation of the newly staged installation from the GNOME app grid was outside the physical test. Generated desktop-entry activation was exercised separately by the hosted private-session fixture.
- **Authorization.** The operator exercised PolicyKit cancellation. A separate denial case remains outside the physical result.
- **Firmware behavior.** The historical low-brightness behavior remains unresolved. Retain the 100% initialization safeguard. Firmware readback verifies Dynamic mode, speed, and brightness. The operator's visual observations are the evidence for effect color and animation. [Native RGB guide](NATIVE-RGB.md#parameter-gating-firmware-verification-and-upgrade-path)
- **Coverage.** Other laptop models, kernel versions, Dynamic effect speeds, and sustained heavy thermal workloads remain unqualified. The firmware fan checks cover the supervised reference workflow.
- **Resume and GNOME observations.** USB controller reinitialization and Wi-Fi RF-kill messages appeared during both resume cycles, with no reported functional failure. GNOME focus activation remains an observation. Historical Ayatana GTK3 deprecation and GTK widget warnings remain documented. [Tray guide](TRAY-INDICATOR.md) · [Warning investigation](https://github.com/tobiasGuta/Nitro-Control/issues/10)

## Release disposition

The Conditional GO applies to a preview with the existing safety guards and the documented limits above. Publication requires independent approval of the release documentation and confirmation that the publication tree matches the qualified implementation. The existing GitHub prerelease points to `087e1b2b02fbc145f502759e20a80d568687886b`. The proposed publication tag is `v0.4.0-preview.5.qualified.1`, which preserves the historical release identity. [Historical prerelease](https://github.com/tobiasGuta/Nitro-Control/releases/tag/v0.4.0-preview.5) · [Release notes and rollback](RELEASE-v0.4.0-preview.5.md)
