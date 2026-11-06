# Native Acer WMI RGB — AN515-58 development preview

> Status: **experimental development feature — NOT yet hardware-qualified for
> arbitrary four-zone colors or reboot/resume**. The earlier *red-only* recovery
> sequence is physically confirmed on this AN515-58 under Fedora 44 and Fedora
> 45. The new configurable backend must be reviewed and tested separately.

This feature builds on the existing, manually verified
[`experiments/acer-wmi-rgb-enable-poc`](../experiments/acer-wmi-rgb-enable-poc/README.md).
It keeps the stock `acer_wmi` driver loaded. It is intentionally **not** the
Linuwu-Sense replacement driver and does **not** install a startup service.

## Verified evidence vs unverified assumptions

**Observed on the reference Nitro AN515-58, Fedora 45, kernel
`7.2.15-300.fc45.x86_64`, November 6, 2026:**

- With stock `acer_wmi`, no keyboard LEDs are listed in `/sys/class/leds`;
  Nitro Control reports no native sysfs RGB writer.
- Stopping the experimental Linuwu service and unloading/reloading `acer_wmi`
  does **not** illuminate the keyboard.
- Running the existing `restore-after-boot.sh` after building its PoC for the
  running kernel physically lights **all four zones red**.
- After that manual recovery `acer_wmi` remains loaded, `fan1=2307 RPM`,
  `fan2=2205 RPM`, and the firmware platform profile is `balanced`.
- Both Fn+F9 and Fn+F10 adjust physical brightness while all zones remain red.

The successful recovery invokes the Acer gaming WMI interface:

1. Method 5: gaming-system-info poll.
2. Method 2: enable four keyboard lighting zones (do **not** confuse with
   read-only method 4; the older PoC did).
3. Method 20: set static mode and brightness.
4. Method 6: set per-zone colors using an 8-byte, endian-aware payload.
5. Method 7: read back per-zone firmware RGB data.

**Not yet established:** which step alone revives the LEDs; arbitrary
four-color use of the new backend; thermal behavior over long sessions;
suspend/resume; automatic boot recovery; compatibility with other Nitro models,
other kernels or unmodified upstream driver modules. Firmware readback does
**not** prove that actual LEDs changed.

## Architecture and trust boundaries

```text
GTK RGB editor (normal user)
    | choose four #RRGGBB colors + brightness (1..100)
    | explicit user confirmation
    v
RGBClient -> pkexec /usr/local/libexec/nitro-control-rgb-helper
    | fixed root-owned Python copy; validated JSON, model, WMI and Acer fans
    | sha256 + owner/mode + modinfo + running-kernel checks
    v
insmod /usr/lib/modules/$(uname -r)/extra/nitro-control/nitro_rgb_enable_poc.ko
       apply=1 colors=RRGGBB,RRGGBB,RRGGBB,RRGGBB brightness=N
    | one-shot WMI 5 / 2 / 20 / 6, then method-7 firmware readback
    v
rmmod nitro_rgb_enable_poc    (always attempted)
    | verify Acer fan telemetry/profile still exist; report outcome
    v
GUI status + REQUIRED physical keyboard inspection
```

- No root GUI, no unprivileged direct WMI, no fan PWM writes, no power-profile
  modifications, no `acer_wmi` blacklist/unload, no `make install`.
- Requires exact DMI manufacturer/product, the numbered gaming-WMI GUID,
  running stock `acer_wmi`, Acer fan1/fan2 telemetry, default
  `acer_wmi.force_caps`, no active Linuwu module, and the firmware platform
  profile interface.
- A privileged helper validates the root-owned, current-kernel-only module,
  its SHA-256 digest, its `modinfo` name/vermagic, and a serializing file lock.
- Polkit authorization and GUI confirmation are required per physical change,
  unless short-lived authorization caching was *separately* opted into.
- The PoC is a kernel module: even an exact-model guard cannot eliminate
  firmware/driver bugs or a hard lockup. Keep backups and save your work.

The older `--rgb-probe` checks existing Linux multicolor LEDs and Linuwu
sysfs endpoints first. The new `native-wmi` backend is detected only when its
optional current-kernel module is staged and stock Acer HWMON prerequisites
hold. Detection is read-only and does **not** load a module.

## Installation (explicit and disabled by default)

**Do not follow this on another laptop model.** This is intended only for
the confirmed four-zone Acer Nitro AN515-58 reference hardware. Review local
source changes and the final diff first.

From a checkout of this feature branch, with matching
`kernel-devel-$(uname -r)`, compiler, `make`, and Secure Boot disabled:

```bash
cd /mnt/Development/Tools/Nitro-Control
git status --short
cd experiments/acer-wmi-rgb-enable-poc
make clean && make
modinfo -F name ./nitro_rgb_enable_poc.ko
modinfo -F vermagic ./nitro_rgb_enable_poc.ko
uname -r
```

Expect the name `nitro_rgb_enable_poc` and an exact running-kernel match for
the first `vermagic` field. **Do not use `make install`.**

Install a **copy** under a fixed, root-owned kernel-pinned location. This
installer does not insert the module:

```bash
cd /mnt/Development/Tools/Nitro-Control
sudo ./scripts/install-native-rgb-fedora.sh \
  /mnt/Development/Tools/Nitro-Control/experiments/acer-wmi-rgb-enable-poc/nitro_rgb_enable_poc.ko

./scripts/install-fedora.sh
sudo ./scripts/install-rgb-helper-fedora.sh
```

The last command **reinstalls the updated** root-owned Polkit RGB helper,
including `native_rgb.py`. The independent, legacy Linuwu helper/service
should stay stopped/disabled. Do not run both backends at once.

Verify only the read-only capability first:

```bash
systemctl is-active nitro-control-rgb-driver.service
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
~/.local/bin/nitro-control --rgb-probe
```

Expected with the correct staged module: `"backend": "native-wmi"` and
`"available": true`. The presence of the WMI GUID alone is insufficient.

## First authorized GUI test

1. Save your work; close other hardware-control apps.
2. Open `~/.local/bin/nitro-control` **as your normal user**.
3. Pick four modest colors (e.g. a uniform green) and 25% brightness.
4. Use **Restore lighting** or **Apply to keyboard** (never “Apply preview”).
5. Confirm the GUI warning; authorize the dedicated Polkit action.
6. Inspect the actual keyboard, Fn+F9/Fn+F10, fan RPM values and platform
   profile. **Do not assume a green success message guarantees LEDs lit up.**
7. Confirm that the one-shot test module is no longer resident:

```bash
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
cat /sys/firmware/acpi/platform_profile
for d in /sys/class/hwmon/hwmon*; do
  if [ "$(cat "$d/name" 2>/dev/null)" = acer ]; then
    echo "$d"
    cat "$d"/fan{1,2}_input
  fi
done
sudo journalctl -k -b --no-pager | grep nitro_rgb_enable_poc | tail -n 30
```

Expected loaded driver: `acer_wmi` only. Any failed `rmmod` is a blocker:
do not run another test until you understand and recover the module state.

## Recovery, uninstall, and kernel upgrades

The manually verified **red-only** recovery remains available as a separate
fallback (it sets all zones **red at 100%** and does not restore previous RGB):

```bash
cd /mnt/Development/Tools/Nitro-Control/experiments/acer-wmi-rgb-enable-poc
sudo bash ./restore-after-boot.sh
```

Run it only with its current-kernel-built PoC, stock `acer_wmi`, no Linuwu,
and the original safety preconditions. After a kernel update, rebuild first.
It is NOT a routine boot service.

Remove only the new native module/digest using:

```bash
cd /mnt/Development/Tools/Nitro-Control
sudo ./scripts/uninstall-native-rgb-fedora.sh
```

To remove the independent Polkit helper as well:

```bash
sudo ./scripts/uninstall-rgb-helper-fedora.sh
```

This does not remove `acer_wmi` or the per-user application. The legacy
Linuwu kernel module, if installed in a separate location, is managed by
its own `uninstall-rgb-driver-fedora.sh` script.

On kernel upgrade the new module must be rebuilt/reviewed and **explicitly
reinstalled for the currently running kernel**. There is no DKMS, unattended
build, kernel blacklist, boot service, startup hook, or GRUB modification.

## Troubleshooting matrix

| Symptom | First check | Action |
| --- | --- | --- |
| `backend: none` | `uname -r` and kernel-pinned module path | Rebuild/install for exact running kernel; do not reuse old `.ko` |
| GUI physical Apply disabled | `--rgb-probe` and root-owned Polkit helper | Reinstall helper explicitly; relaunch GUI |
| “Linuwu driver loaded” | `lsmod`, service status | Stop via original managed service, never force-load both |
| “Acer HWMON unavailable” | Acer fan sensors | Stop tests; investigate Acer driver before changing firmware |
| “module digest mismatch” | Root-installed module versus digest | Reinstall from reviewed, rebuilt local source |
| “firmware write failed” | Kernel logs, module still loaded? | Collect log; do not retry blindly |
| Readback says success but LEDs dark | Actual keyboard, Fn brightness | Record results; do not misreport physical success |
| `rmmod` failure | `lsmod` and kernel journal | Stop further tests; recover or reboot to stock driver |

## Testing and release gates

Offline validation:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nitro_control tests
bash -n scripts/install-native-rgb-fedora.sh scripts/uninstall-native-rgb-fedora.sh
```

These are **synthetic tests**. They do not prove kernel compilation, real WMI,
Polkit, GUI rendering, reboot or suspend safety. On the AN515-58, test:

- [ ] Custom WMI module compiles on currently booted kernel.
- [ ] Controlled 25% uniform color visibly works.
- [ ] Four independent colors visibly match user selection.
- [ ] Fn brightness keys still work.
- [ ] Stock `acer_wmi`, HWMON fan1/fan2 and platform profiles remain normal.
- [ ] A denied Polkit prompt results in no physical change.
- [ ] An invalid/missing module or digest never initiates a WMI call.
- [ ] No one-shot module remains loaded after success or failure.
- [ ] Suspend/resume is tested deliberately, with work saved.
- [ ] Reboot/login behavior is tested deliberately, with work saved.

**No automatic startup/resume restore is included in this preview.** Once
those gates pass, a separate reviewed and explicitly enabled startup service
can be considered. There is no guarantee that a new kernel will be compatible.

## Relationship to the older Linuwu backend

The legacy `nitro-control-rgb-driver.service` swaps `acer_wmi` with a
third-party Linuwu module *on demand*. The new path uses a distinct root-owned
`nitro_rgb_enable_poc.ko` only during each authorized operation, leaving
`acer_wmi` loaded throughout. Linuwu is a separate experimental fallback,
not a dependency for native RGB. The GUI prefers supported native Linux
multicolor LED sysfs interfaces when present, then an active Linuwu sysfs
writer, then this opt-in native WMI path.

## Protocol and safety references

- Original PoC and physically confirmed single-machine results:
  [experiment README](../experiments/acer-wmi-rgb-enable-poc/README.md)
- [Hardware safety and Linuwu integration](RGB-HARDWARE.md)
- [Linux kernel multicolor LED interface](https://docs.kernel.org/leds/leds-class-multicolor.html)
- [Acer AN515-58 kernel RGB RFC](https://www.spinics.net/lists/kernel/msg6183499.html)
