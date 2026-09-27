# Nitro Control

**A read-only GNOME dashboard for Acer Nitro laptops, initially tested against the AN515-58 interface.**

Version 0.1.0 • Python 3.11+ • MIT license

Nitro Control is a small Python + GTK4 + Libadwaita application using native Linux APIs. v0.1.0 monitors your system; it does **not** control fans, change power profiles, send WMI commands, modify kernel modules, or require root.

## v0.1.0 capabilities

- Live CPU package temperature from an *identified* `coretemp` / `k10temp` sensor.
- Acer fan RPM and automatic/manual mode reporting through `/sys/class/hwmon`.
- Acer firmware temperatures, with **unidentified** labels preserved rather than guessing their physical role.
- NVIDIA GPU temperature, utilization, VRAM, and power usage via `nvidia-smi` when available.
- NVMe drive temperatures, current firmware platform profile and its allowed choices, current TuneD profile.
- RGB WMI interface discovery, including numbered instances such as `...-8`, and indication of whether a driver is bound.
- Periodic refresh on a background thread; missing sensors are displayed as unavailable.
- Clearly labeled demo mode and a one-shot JSON diagnostic mode.

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

The built-in demo values are **examples, not readings from your device**. If `nvidia-smi` is unavailable, the NVIDIA panel reports unavailable and the rest of the application continues working.

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

Tests use a disposable simulated sysfs tree shaped like the AN515-58. They cover Acer fans and temperature sensors, NVIDIA CLI parsing and failure cases, firmware choices, WMI instance suffixes, driver binding, and no-write behavior. GUI rendering requires a Fedora/GNOME environment with the system GI libraries; CI does not claim to test the GNOME window.

## Safety and privacy

- Hardware collection is read-only and runs as your normal desktop user.
- No telemetry, network requests, external extensions, custom drivers, root GUI, fan curves, or EC/WMI command calls.
- JSON output includes device model and measurements, not hostname, machine ID, MAC address, or exact filesystem paths.
- Linux `pwmN_enable >= 2` conventionally means automatic control. The app **reports** this without writing `pwmN` or inferring a percentage from its value.
- An Acer WMI GUID can be present on a device with different physical keyboard variants. Its presence is **not** proof of four-zone RGB.

## Project layout

```text
nitro_control/
  __main__.py       CLI, demo, JSON, GUI launch
  ui.py             GTK4/Libadwaita dashboard
  hardware.py       isolated read-only hardware adapters
  models.py         typed immutable snapshots
  demo.py           explicitly labeled example data
scripts/            per-user install and uninstall
 tests/             synthetic hardware tests
```

## Roadmap (not implemented)

- v0.2: Optional, authorized performance-profile switching with explicit user confirmation.
- v0.3: Investigate native keyboard RGB support; verify actual keyboard variant and driver behavior before any write operation.
- Later: Packaging and broader hardware testing. No requirement to replace a functioning native `acer_wmi` driver.

## References

- [Linux hwmon sysfs](https://docs.kernel.org/hwmon/sysfs-interface.html)
- [Linux platform-profile API](https://docs.kernel.org/userspace-api/sysfs-platform_profile.html)
- [PyGObject GTK4 introduction](https://pygobject.gnome.org/tutorials/gtk4/introduction.html)
- [Libadwaita application window](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/class.ApplicationWindow.html)
- [NVIDIA System Management Interface](https://docs.nvidia.com/deploy/nvidia-smi/)

## License

MIT. See `LICENSE`.
