# Nitro Control

**A native GNOME dashboard for Acer Nitro laptops, initially tested against the AN515-58 interface.**

Version 0.3.0-preview.2 • Python 3.11+ • MIT license

Nitro Control is a small Python + GTK4 + Libadwaita application using native Linux APIs. Desktop power-mode switching uses Fedora’s standard D-Bus service. v0.3.0-preview.2 adds optional, explicitly authorized physical RGB support **only when a supported kernel sysfs endpoint already exists**. It never replaces drivers, calls WMI directly, changes fan PWM, or runs the GUI as root.

## v0.3: RGB studio and gated physical backend

The reference AN515-58 has a user-confirmed four-zone RGB keyboard, and Linux
exposes its numbered RGB WMI GUID. **No RGB writer is currently exposed on this
Fedora 44 kernel**, so the physical Apply button will remain disabled. The GUID
alone does not provide a safe control endpoint.

The studio has four GTK color selectors, brightness, presets, a simulated
Apply preview/Reset, and an optional physical Apply button. Preview remains
strictly in-memory. A separate, root-owned one-shot helper can be installed
explicitly *after* a supported native LED or Linuwu sysfs endpoint exists. The
GUI asks confirmation for each physical change; the optional Polkit helper
requires administrator authorization (with opt-in short-lived caching). No
color persistence, auto-restore, or module installation yet.

## v0.2.0 capabilities

- Live CPU package temperature from an *identified* `coretemp` / `k10temp` sensor.
- Acer fan RPM and automatic/manual mode reporting through `/sys/class/hwmon`.
- Acer firmware temperatures, with **unidentified** labels preserved rather than guessing their physical role.
- NVIDIA GPU temperature, utilization, VRAM, and power usage via `nvidia-smi` when available.
- NVMe drive temperatures, current firmware platform profile and its allowed choices, current TuneD profile.
- RGB WMI interface discovery, including numbered instances such as `...-8`, and indication of whether a driver is bound.
- Periodic refresh on a background thread; missing sensors are displayed as unavailable.
- Clearly labeled demo mode and a one-shot JSON diagnostic mode.
- Optional GNOME power-mode selection through `org.freedesktop.UPower.PowerProfiles`; only modes actually advertised by the service are offered.
- Explicit Apply and confirmation dialog, asynchronous D-Bus change, re-read verification, and gracefully disabled controls when the service is unavailable.
- Long Acer firmware profile names now wrap instead of being cut off in a narrow suffix.

### Compatibility

The initial reference machine is Acer Nitro AN515-58, Fedora 44, GNOME Wayland, kernel 7.2.7, Intel Iris Xe + RTX 3050 Ti. Other Linux laptops can run the app; unsupported Acer features will simply be shown as unavailable. This is **not** a guarantee of compatibility with other models or releases.

## Run from a checkout on Fedora

Install the native libraries (no `pip` or privileged application process needed):

```bash
sudo dnf install python3-gobject gtk4 libadwaita
```

From the project directory:

```bash
/usr/bin/python3 -m nitro_control --demo      # sample values; no hardware changes
/usr/bin/python3 -m nitro_control             # live dashboard
/usr/bin/python3 -m nitro_control --once      # JSON snapshot without GTK
```

The built-in demo values are **examples, not readings from your device**. If `nvidia-smi` is unavailable, the NVIDIA panel reports unavailable and the rest of the application continues working. Demo mode does not permit power changes.

### Upgrade an existing installation

The installer copies source into the per-user application directory. After pulling the new version, rerun it and relaunch the window:

```bash
git pull --ff-only
./scripts/install-fedora.sh
```

### Add to GNOME's app launcher

From the project directory:

```bash
./scripts/install-fedora.sh
```

This installs a *per-user copy* into `~/.local/share/nitro-control`, a launcher under `~/.local/bin`, and a `.desktop` file. No `sudo` is needed for the installation script once the native libraries above are present. Open **Nitro Control** from the GNOME app grid, or run `~/.local/bin/nitro-control`.

Remove only these installed copies with `./scripts/uninstall.sh`. This does not remove your source checkout or dependencies.

## Tests

The collector and JSON mode use only the Python standard library; GTK is imported only for the GUI. Run these on any Python 3.11+ system:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nitro_control tests
python3 -m nitro_control --demo --once
```

Tests use a disposable simulated sysfs tree shaped like the AN515-58, plus an injected fake power-profile service. They cover Acer fans and temperatures, NVIDIA CLI parsing and failures, firmware choices, WMI suffixes, driver binding, read-only monitoring, allowed desktop modes, denied changes, and verification of service-reported results. GUI rendering and live D-Bus/Polkit behavior require a Fedora/GNOME environment; CI does not claim to test the GNOME window or change a real power mode.

## Safety and privacy

- Hardware monitoring is read-only and runs as your normal desktop user. Power-mode changes occur only after explicit user confirmation.
- No telemetry, network requests, custom drivers, root GUI, fan curves, or direct EC/WMI calls. The RGB preview is in-memory only; the optional helper writes strictly known RGB sysfs attributes.
- The desktop power-mode operation uses only the standard D-Bus `ActiveProfile` property. The optional RGB path invokes a **root-owned** one-shot helper through `pkexec`; nothing privileged is imported from the user-writable GUI installation.
- Power Saver, Balanced, and Performance are desktop modes. The five Acer firmware-supported names remain read-only; do not assume a one-to-one mapping.
- JSON output includes device model and measurements, not hostname, machine ID, MAC address, or exact filesystem paths.
- Linux `pwmN_enable >= 2` conventionally means automatic control. The app **reports** this without writing `pwmN` or inferring a percentage from its value.
- An Acer WMI GUID can be present on a device with different physical keyboard variants. Its presence is **not** proof of four-zone RGB.

## Project layout

```text
nitro_control/
  __main__.py       CLI, demo, JSON, GUI launch
  ui.py             GTK4/Libadwaita dashboard
  hardware.py       isolated read-only hardware adapters
  profiles.py       optional desktop power-mode service with validation
  rgb.py            validated four-zone plans and in-memory preview backend
  rgb_hardware.py   exact-model, exact-endpoint sysfs RGB adapters
  rgb_client.py     unprivileged client for optional Polkit helper
  rgb_privileged.py root-owned, one-shot RGB entry point
  models.py         typed immutable snapshots
  demo.py           explicitly labeled example data
scripts/            per-user install and uninstall
 tests/             synthetic hardware tests
```

## Roadmap

- v0.2: Desktop power-mode switching through the standard system service, with confirmation.
- v0.3 preview.2: Simulated lighting editor, read-only RGB probe, gated hardware adapters and optional Polkit helper.
- Next: Validate a real driver interface and actual keyboard behavior on the reference Fedora laptop.
- Later: Packaging and broader hardware testing. No requirement to replace a functioning native `acer_wmi` driver.

## References

- [Linux hwmon sysfs](https://docs.kernel.org/hwmon/sysfs-interface.html)
- [Linux platform-profile API](https://docs.kernel.org/userspace-api/sysfs-platform_profile.html)
- [PyGObject GTK4 introduction](https://pygobject.gnome.org/tutorials/gtk4/introduction.html)
- [Libadwaita application window](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ApplicationWindow.html)
- [NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/)
- [Power Profiles D-Bus interface](https://upower.pages.freedesktop.org/power-profiles-daemon/gdbus-org.freedesktop.UPower.PowerProfiles.html)
- [Linux multicolor LED userspace ABI](https://kernel.org/doc/html/next/leds/leds-class-multicolor.html)
- [AN515-58 RGB kernel RFC (not a shipped driver guarantee)](https://www.spinics.net/lists/kernel/msg6183499.html)

## License

MIT. See `LICENSE`.


## Optional physical RGB backend (v0.3.0-preview.2)

The four-zone studio continues working **without any new driver or privilege**. A
**read-only** diagnostic checks your actual hardware:

```bash
/usr/bin/python3 -m nitro_control --rgb-probe
```

On the original reference Nitro AN515-58 with Fedora 44 kernel 7.2.7, the
numbered Acer RGB WMI GUID exists but no RGB writer is exposed. The expected
result is `"available": false`: **the physical Apply button remains disabled**.
This is a capability gap, not an application crash. Do not mistake a visible WMI
GUID for a driver that can safely accept keyboard commands.

The optional adapters recognize *only* these existing, documented sysfs
interfaces, on the exact model and WMI GUID:

- Four native `acer-wmi::kbd_backlight_1` through `_4` Linux multicolor LED
  devices. Each must have validated `multi_index`, `multi_intensity`,
  `brightness`, and `max_brightness` attributes.
- Linuwu Sense's documented `four_zoned_kb/per_zone_mode` endpoint, accepting
  exactly four RGB hex colors and brightness.

Native multicolor LEDs are preferred if both are present. Unsupported, partial,
malformed, or absent backends fail closed. No unknown GUID calls, direct EC
access, kernel compilation, module removal, fan writes, or automatic driver
installation are included. Fan monitoring and desktop power modes are unchanged.

**Once a supported writer exists**, an optional administrator-owned helper may
be installed *explicitly* from a reviewed source checkout:

```bash
sudo ./scripts/install-rgb-helper-fedora.sh
```

This copies a minimal, standalone Python helper and dependencies into root-owned
`/usr/local/libexec` and installs a dedicated PolicyKit action. It never imports
modules from the user's writable installation. The GUI still runs as your user,
requires a separate confirmation per hardware change, and invokes the fixed
helper through `pkexec`. By default, the action uses `auth_admin` and prompts on every hardware apply.
For an explicit, short-lived Polkit authorization cache (typically about five
minutes), reinstall the helper with:

```bash
sudo ./scripts/install-rgb-helper-fedora.sh --cache-authorization
```

This selects `auth_admin_keep` **only for Nitro Control's dedicated RGB action**;
it is not permanent passwordless root access. The GUI still asks confirmation
for every physical change, and the root helper continues to validate exact
model, endpoint and four-zone input. Cache reuse depends on the running Polkit
version/session; another password prompt may still occur. Return to a password
on every change by reinstalling with no option. Errors or denied authorization
are displayed as failures. To remove it:

```bash
sudo ./scripts/uninstall-rgb-helper-fedora.sh
```

A successful sysfs readback is only **software-level verification**: visually
confirm the keyboard really changed. Some firmware reports accepted values
without changing LEDs. There is no auto-restore on reboot or suspend/resume yet.
Read [the hardware safety and validation guide](docs/RGB-HARDWARE.md) before
trying a third-party driver or kernel patch.
