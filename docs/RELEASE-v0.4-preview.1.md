# Nitro Control v0.4.0-preview.1 — native Acer WMI RGB

**Status: experimental, opt-in preview.** This is a source version, **not an
automatically published binary or package**. It is intended for the original
Acer Nitro AN515-58 four-zone RGB reference device. The preview does **not**
ship a replacement for upstream Linux `acer_wmi` and is **not yet suitable
for unqualified deployment across other Nitro laptops**.

## Hardware result and scope

The reference hardware owner reported the following on **November 6, 2026**,
Fedora 45, kernel `7.2.15-300.fc45.x86_64`:

| Test | Result |
| --- | --- |
| Native Acer gaming WMI methods 5/2/20/6 with method-7 readback | Accepted |
| Four distinct physical RGB zones via the GTK editor | Pass |
| Fn+F9 / Fn+F10 after RGB initialization at WMI brightness **100** | Pass |
| Acer `acer_wmi`, HWMON fan telemetry and `balanced` profile after RGB apply | Pass |
| Fans physically running | Pass |
| One suspend/resume cycle preserves lighting and colors | Pass |
| One reboot preserves colors before GDM login page and after login | Pass |
| Green at WMI brightness **65** with repeated Fn+F9 | **Known regression:** visible colors change unexpectedly |
| Cold boot behavior on future kernels, different machines, and repeated cycles | Unverified |

A firmware readback by itself does **not** establish that physical RGB LEDs
changed. The manual owner confirmations above are separate visual observations.
The kernel module was observed to unload after a successful native operation.

## Architecture

The standard Linux `acer_wmi` driver **stays loaded** for the entire workflow.
The normal-user GTK4 RGB editor requests a one-time Polkit authorization through
the fixed, root-owned `nitro-control-rgb-helper`. That helper verifies exact
DMI model and Acer gaming WMI GUID, available Acer fan telemetry, lack of a
conflicting Linuwu module, a SHA-256-validated current-kernel module with
root ownership and expected permissions, and kernel identity via `modinfo`.
It then loads `nitro_rgb_enable_poc.ko` to perform an explicitly requested
firmware color transaction and **attempts to unload it immediately**, even
when module insertion fails or a partial load remains visible.

The kernel's own PoC validates the exact model and GUID and independently
refuses native `apply=1` brightness values other than **100**. The Python
helper also refuses these values before any module operation. The GUI
disables native Apply/Restore until the editor is at 100; after applying,
use Fn+F9/Fn+F10 for actual brightness control. Other legacy sysfs backends
do not use this workaround.

This is a firmware-writing kernel module; an unexpected WMI/ACPI error or
system hang remains possible. **No fan PWM/EC writes, boot autostart, kernel
blacklist, GRUB changes, DKMS or unattended resume/reboot restore are included.**
A WMI operation is **not transactional**: a failure after one or two zone
writes can leave partially changed colors. Physical state may survive after
module unload, as observed.

## Installing from reviewed source on Fedora

From a checkout of this exact preview (not an unreviewed arbitrary branch),
first ensure the laptop is **Acer Nitro AN515-58**, Secure Boot is disabled,
and the running kernel matches its installed `kernel-devel`. Stop the old
`nitro-control-rgb-driver.service` before this procedure if active; do
**not** unload stock `acer_wmi` or load Linuwu.

```bash
cd /mnt/Development/Tools/Nitro-Control
git status --short
uname -r
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
systemctl is-enabled nitro-control-rgb-driver.service
systemctl is-active nitro-control-rgb-driver.service || true

cd experiments/acer-wmi-rgb-enable-poc
make clean && make
modinfo -F name ./nitro_rgb_enable_poc.ko
modinfo -F vermagic ./nitro_rgb_enable_poc.ko
cd ../..

# These steps are explicit root opt-ins. Review the source before sudo.
sudo bash ./scripts/install-native-rgb-fedora.sh \
  "$PWD/experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.ko"
bash ./scripts/install-fedora.sh
sudo bash ./scripts/install-rgb-helper-fedora.sh

~/.local/bin/nitro-control --rgb-probe
```

The expected backend is `native-wmi` only when its kernel-pinned module
and the stock Acer interfaces are available. The `--rgb-probe` command is
read-only; it does not light the keyboard. For actual RGB changes, open
Nitro Control as your normal user, choose four colors, **set the slider
to 100**, select **Apply to keyboard** or **Restore lighting**, confirm,
authorize Polkit, and visually inspect the keyboard afterward.

For a checkout at a different path, replace the example `cd` path with
its real directory. Do not use Linuwu's `make install`.

## Verification after physical Apply

```bash
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
cat /sys/firmware/acpi/platform_profile
for d in /sys/class/hwmon/hwmon*; do
  if [ "$(cat "$d/name" 2>/dev/null)" = acer ]; then
    cat "$d"/fan{1,2}_input
  fi
done
sudo journalctl -k -b --no-pager | grep nitro_rgb_enable_poc | tail -n 30
```

The expected normal state after success is stock `acer_wmi` loaded,
Linuwu absent, and the native PoC module unloaded. If an unload fails,
**do not attempt another firmware operation**. Inspect the journal and
module state; reboot to a known-good stock configuration if necessary.

## Security/failure checks

Automated tests cover exact-model and GUID refusal; absent fans; conflict
detection; wrong kernel version; corrupt checksum; symlinked digest;
root-only authorization; lower-than-100 brightness; locked operations;
failed or partial module insertion; unload failure; denied `pkexec` responses;
and malformed helper responses. These are **synthetic CI tests**. Live
forced-failure testing on the laptop is not yet claimed. Do not simulate
failure by corrupting installed modules on a working machine.

Review questions for future production readiness: process cancellation;
a deliberate reboot while a firmware operation is active; repeated
suspend/hibernation cycles; multiple kernel versions; and independent
kernel subsystem maintainer review.

## Rollback and upgrades

```bash
cd /mnt/Development/Tools/Nitro-Control
sudo bash ./scripts/uninstall-native-rgb-fedora.sh
sudo bash ./scripts/uninstall-rgb-helper-fedora.sh
```

Uninstall refuses to remove an active native PoC. These commands do not
remove the original `acer_wmi` driver or change boot configuration.
The separate legacy Linuwu module and service have their **own** uninstall
script; they are never automatically re-enabled by this preview.

The RGB state can survive module removal. A failed partial apply may
require reapplying a known-good configuration or a reboot. The previously
verified `restore-after-boot.sh` gives a manual all-red fallback at
brightness 100, **but only when rebuilt for the running kernel with the
original exact-model and fan safeguards**.

A Fedora kernel upgrade invalidates the pinned native module. Do not
copy a `.ko` from an old kernel. Review and rebuild for the *running*
kernel, then reinstall explicitly. No background updater/autobuilder exists.

## Remaining scope and release policy

- This preview is verified on **one** AN515-58 Fedora 45 configuration.
- Only WMI initialization brightness **100** is currently allowed on
  the experimental native backend; the 65% firmware regression remains open.
- Physical Fn brightness remains separate from the GUI initialization slider.
- RGB surviving one reboot **before GDM** and a suspend/resume cycle means
  no automatic boot recovery is needed for the reference case.
- Merge into `main` does **not** install the privileged helper, load a
  module, publish a release artifact, or enable a systemd unit.

For full protocol notes, preflight checks and validation history, see
[NATIVE-RGB.md](NATIVE-RGB.md). For the observed low-brightness regression,
see [issue #2](https://github.com/tobiasGuta/Nitro-Control/issues/2).
