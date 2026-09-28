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
`balanced` platform profile, and NVIDIA readings were present.

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
because no persistent module configuration changes are made. A Polkit prompt
is required for the actual GUI write. Inspect the physical keyboard.

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
root. Physical Apply requires a separate user confirmation and PolicyKit
administrator authorization on every invocation.

## Research references

- AN515-58 native RGB RFC: https://www.spinics.net/lists/kernel/msg6183499.html
- Standard LED ABI: https://docs.kernel.org/leds/leds-class-multicolor.html
- Linuwu Sense docs (third-party module, GPLv3): https://github.com/PXDiv/Div-Linuwu-Sense

These are protocol/interface references, not endorsements to install or replace
a currently working kernel driver. The RFC includes a reported brightness
behavior issue; later integration into your exact installed kernel must be
verified rather than assumed.
