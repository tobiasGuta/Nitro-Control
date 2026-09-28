# AN515-58 physical keyboard RGB: development and safety guide

## Known local baseline

Reference device: Acer Nitro AN515-58, four-zone RGB keyboard, Fedora 44,
kernel 7.2.7, Intel Iris Xe + NVIDIA RTX 3050 Ti. `acer_wmi` provides both fan
RPM sensors and firmware performance profiles, and the NVIDIA driver works on
GNOME Wayland. The numbered WMI instance
`7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56-8` is present, **but** no RGB driver
is bound and no multicolor keyboard LED nodes or Linuwu RGB sysfs file exist.

**This means physical keyboard lighting is not yet operable through Nitro
Control on this exact baseline.** The GUI simulation and power controls remain
available. Installing the optional helper alone cannot create an RGB driver.

## Safe progression

1. Leave NVIDIA, `acer_wmi`, SELinux and existing power management intact.
2. Back up the development drive and important files before considering any
   custom kernel changes. The development drive was observed as unencrypted
   Btrfs, so consider encryption only with a verified backup/recovery plan.
3. Use `python3 -m nitro_control --rgb-probe` to confirm the current backend.
4. If a supported RGB sysfs endpoint becomes available from an independently
   validated driver, install the optional root-owned helper explicitly with
   `sudo ./scripts/install-rgb-helper-fedora.sh`.
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
