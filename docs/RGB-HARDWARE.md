# AN515-58 physical keyboard RGB: development and safety guide

## Validated temporary hardware test (AN515-58)

Reference device: Acer Nitro AN515-58, four-zone RGB keyboard, Fedora 44,
kernel 7.2.7-200.fc44.x86_64, Intel Iris Xe + NVIDIA RTX 3050 Ti.
The stock `acer_wmi` exposes fan RPM and firmware performance profiles but not
a four-zone keyboard RGB writer. The exact numbered WMI instance
`7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56-8` is present.

On this machine, the user compiled Div-Linuwu-Sense revision
`d8ea437d847268dd9fe2a49ae28d0723dd720968` against the running kernel,
locally replaced three legacy `strncpy()` calls with `memcpy()` plus
`linux/string.h`, and added explicit nonzero/bounded input-length checks.
A temporary `insmod` test exposed `four_zoned_kb/per_zone_mode`; a uniform
low-brightness green test and a red/green/blue/purple four-zone test both
visibly worked. The original `393651,393651,393651,393651,100` lighting was
restored and the stock `acer_wmi` was reloaded. Subsequent fan readings,
`balanced` platform profile, and NVIDIA readings were present. A later GUI
session restored lighting and the stock module but ended in `low-power` firmware
profile; that may reflect an intentional desktop power-mode change and is not
proof that the profile was preserved through the session.

These results establish this single machine's temporary RGB behavior, **not**
suspend/resume, reboot, new-kernel, or permanent-driver compatibility. The
upstream module still replaces the native Acer driver when installed.

## Temporary GUI integration test

The app already contains an optional root-owned Polkit RGB helper and physical
Apply button. The session script leaves driver installation, blacklisting,
boot settings and module autoload unchanged.

From the Nitro Control repo, explicitly install only the optional helper:

```bash
sudo ./scripts/install-rgb-helper-fedora.sh
```

Close other Nitro Control windows, then run from your normal graphical desktop
terminal (not `sudo bash`):

```bash
./scripts/test-rgb-gui-session-fedora.sh
```

It requires the locally patched `.ko` in
`/mnt/Development/Tools/Div-Linuwu-Sense-build-test/src/linuwu_sense.ko`;
an alternate full `.ko` path may be passed as the first argument. It checks
model, kernel version, helper and module state, opens one temporary driver
session, saves existing lighting, probes the Linuwu endpoint, launches the
unprivileged application, and restores lighting and stock `acer_wmi` on exit.
The GUI time limit is four minutes. It cannot recover from a kernel crash,
hard kill, or sudden power loss; a normal reboot should load the stock module
because no persistent module configuration changes are made. By default,
each actual GUI write requires Polkit administrator authentication. To opt into
a short-lived authorization cache, reinstall the helper with
`sudo ./scripts/install-rgb-helper-fedora.sh --cache-authorization`. This changes
only the dedicated Nitro Control RGB action to `auth_admin_keep` (typically
about five minutes); per-write GUI confirmation and strict helper validation
remain. Cache reuse is not guaranteed on every Polkit implementation. Restore
the prompt-every-time policy by reinstalling without the option. Inspect the
physical keyboard.

After the test, inspect `lsmod`, the platform profile and fan sensors.
Only after this GUI test passes should permanent installation be considered.

## Safe progression

1. Leave NVIDIA, `acer_wmi`, SELinux and existing power management intact.
2. Back up the development drive and important files before considering any
   custom kernel changes. The development drive was observed as unencrypted
   Btrfs, so consider encryption only with a verified backup/recovery plan.
3. Use `~/.local/bin/nitro-control --rgb-probe` to confirm the current backend during an explicitly started driver test.
4. For the validated temporary Linuwu session, install the optional root-owned
   helper explicitly with `sudo ./scripts/install-rgb-helper-fedora.sh`.
5. Start with a uniform, low-brightness static color, confirm the Polkit prompt,
   visually verify that the keyboard really changes, and verify fan RPM/profile
   remain normal. Then test four distinct colors.
6. Test suspend/resume and reboot manually; do not enable automatic restoration
   until persistence and failure recovery are tested.

There is no `make install`, `modprobe`, `rmmod`, WMI-method command, direct EC
write, or driver replacement in the Nitro Control installer. Installing Linuwu
Sense separately changes the platform driver and therefore has compatibility
and rollback implications, particularly on Fedora's moving kernel releases.
Do not run an installer that replaces `acer_wmi` just to activate the GUI.

## Supported endpoints

**Native multicolor LED (preferred when available):** exact device names
`/sys/class/leds/acer-wmi::kbd_backlight_{1,2,3,4}` and their standard
`multi_index`, `multi_intensity`, `brightness` and `max_brightness` attributes.
The component order is determined by `multi_index`, not assumed RGB. Native
writes are made zone by zone with best-effort rollback to the previous sysfs
values if a later write or readback fails. This does not guarantee physical
firmware rollback.

**Linuwu Sense (optional, not bundled):** existing `four_zoned_kb/per_zone_mode`
under the documented Acer platform/module sysfs paths. One validated payload
contains four six-digit hex colors plus brightness. We attempt rollback on
failed readback, but the firmware may still differ from the value shown by
sysfs.

In both cases the helper requires the exact model name and a numbered RGB WMI
interface. Only the root-owned helper writes; the desktop process never runs as
root. Physical Apply requires a separate user confirmation every time; the
helper requires administrator authorization, which may be briefly cached only
when explicitly opted into `auth_admin_keep`.

## Research references

- AN515-58 native RGB RFC: https://www.spinics.net/lists/kernel/msg6183499.html
- Standard LED ABI: https://docs.kernel.org/leds/leds-class-multicolor.html
- Linuwu Sense docs (third-party module, GPLv3): https://github.com/PXDiv/Div-Linuwu-Sense

These are protocol/interface references, not endorsements to install or replace
a currently working kernel driver. The RFC includes a reported brightness
behavior issue; later integration into your exact installed kernel must be
verified rather than assumed.

## Opt-in, reversible on-demand driver integration (v0.3.0-preview.3)

The user also validated the full GTK color/preset/brightness -> Polkit ->
Linuwu -> physical four-zone path, with the original colors and stock module
restored after a four-minute temporary session. Authorization caching worked.
**An unbounded normal-use session, suspend/resume and reboot have not been
validated yet.** The next installer is a staged, explicitly opted-in mechanism,
not a claim of permanent driver safety.

This approach does **not** use the upstream `make install`, add a kernel module
blacklist, modify the boot image or enable a service automatically. It copies
only the locally compiled/patched `.ko` into a root-owned, **current-kernel-only**
path; records a SHA-256 digest; installs a root-owned controller and a systemd
oneshot service. The controller refuses unexpected model/WMI, wrong or missing
kernel module, altered digest, and a concurrently loaded Linuwu module. A failed
start attempts to restore `acer_wmi`; successful stop restores the RGB state
captured at start, unloads Linuwu and restores the native driver. The controller
never writes fan or firmware profile controls.

**Save your work and keep a recovery option available.** Experimental kernel code
can crash/hang the system; software traps cannot recover from a hard lockup.
This install requires the already patched, locally tested `.ko`, not the
unpatched upstream code. Install from the Nitro Control checkout:

```bash
cd /mnt/Development/Tools/Nitro-Control
git pull --ff-only
./scripts/install-fedora.sh
sudo ./scripts/install-rgb-driver-fedora.sh \
  /mnt/Development/Tools/Div-Linuwu-Sense-build-test/src/linuwu_sense.ko
systemctl is-enabled nitro-control-rgb-driver.service
```

The service should report `disabled`. The separate Polkit RGB helper must also
be installed (the prior GUI test already installed it). Start the managed driver
**on demand** and probe without using the four-minute test wrapper:

```bash
sudo systemctl start nitro-control-rgb-driver.service
systemctl --no-pager status nitro-control-rgb-driver.service
~/.local/bin/nitro-control --rgb-probe
~/.local/bin/nitro-control
```

Choose colors at low brightness and use **Apply to keyboard**. Closing the GUI
**does not stop the driver or reset colors**; the service manages the module,
not the GUI. Inspect fan speeds and firmware profile. Do not simultaneously use
`test-rgb-gui-session-fedora.sh`, the upstream `make install`, or another module
manager while this service is active.

To finish the on-demand session and restore the initial lighting and native
driver:

```bash
sudo systemctl stop nitro-control-rgb-driver.service
lsmod | grep -E '^(acer_wmi|linuwu_sense)'
cat /sys/firmware/acpi/platform_profile
sensors | sed -n '/acer-isa-0000/,/^$/p'
```

If service stop fails, inspect `journalctl -u nitro-control-rgb-driver.service -b`
and do not force-load `acer_wmi` alongside Linuwu. A normal reboot without boot
activation should return to the stock Acer module. If the service cannot be
stopped cleanly, resolve the error or reboot. To remove the integration entirely:

```bash
sudo ./scripts/uninstall-rgb-driver-fedora.sh
```

It removes only Nitro Control's service/controller/pinned module files and
leaves the independent Polkit helper installed. No general-purpose polkit
passwordless rule, kernel blacklist or third-party boot service is installed.

### Kernel upgrades and boot activation

The module is **not** rebuilt automatically for new kernels. The controller
selects `/usr/lib/modules/$(uname -r)/extra/nitro-control/linuwu_sense.ko` and
checks `vermagic` and SHA-256. With no module for a newly booted kernel,
`ExecCondition` skips the replacement. Stock `acer_wmi` remains unblacklisted.
Rebuild and review the driver source for each new kernel, repeat the temporary
RGB tests, then explicitly reinstall the matching `.ko`.

Only after the new on-demand service passes runtime, suspend/resume and reboot
validation should you **separately decide** whether to enable the service at
boot with `sudo systemctl enable nitro-control-rgb-driver.service`. We do **not**
automatically enable it now. Disable boot startup at any time with
`sudo systemctl disable nitro-control-rgb-driver.service`; disable without
`--now` does not stop an already active service. The native module may load
first and then be replaced during boot if boot activation is enabled.

A module load success and RGB sysfs readback are not evidence that every fan,
power or laptop function remains correct across suspend/resume, new kernels or
reboot. Maintain backups and validate on the actual hardware.
