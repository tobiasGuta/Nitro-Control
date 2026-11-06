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

### New Fedora 45 hotkey regression report — November 6, 2026

During the first v0.4 authorized native WMI RGB application:

- The module performed the full enable and color sequence, including four
  **green** zone writes (WMI method 6), with matching method-7 firmware readback.
- **The actual brightness was 65**, as shown by the kernel log, not the
  previously suggested 25% test profile.
- After applying, the original `acer_wmi` remained loaded, fan inputs reported
  2272/2222 RPM, the platform profile was `balanced`, and the temporary
  RGB module was unloaded.
- **User-observed regression:** repeated presses of **Fn+F9** initially dim
  the keyboard but subsequently cause unexpected changes to keyboard colors,
  rather than dimming consistently to off.
- Neither the exact keypress count nor the LED colors or WMI state after the
  transition have yet been recorded. This is NOT documented as a fixed bug.

**Critical A/B clarification:** Earlier on this same kernel the *manual red-only*
`restore-after-boot.sh` sequence (RGB `FF0000` in all zones, WMI brightness
100) **left Fn+F9/Fn+F10 working normally**. The unexpected color changes
were observed **only after the new configurable Nitro-Control GUI apply**
(`00FF00` in all zones, WMI brightness 65). Both workflows use WMI methods
5, 2, 20, and 6. Prioritize **color/brightness interaction in our new
configuration**, not a presumed Fedora/global hotkey issue.

**A/B test update, November 6, 2026:** The user applied **all four
zones green (`#00FF00`) at WMI brightness 100 through the v0.4 GUI**. Both
Fn+F9 and Fn+F10 then worked normally **without unexpected color changes**.
This matches the old red/100 recovery and contrasts with the green/65 test.
This strongly implicates the chosen WMI brightness value or its interaction
with the firmware. It does **not** establish an exhaustive supported range,
prove that arbitrary zone combinations are safe, or isolate the exact firmware
cause.

**Additional hardware validation, November 6, 2026:** The owner tested
**a different keyboard color through Nitro-Control v0.4 at WMI brightness
100** and confirmed that both the color application and Fn brightness
behavior worked as expected. The exact additional color and whether all
four zones were independently configured were not reported. This is
further evidence for the 100%-initialization workaround, **not yet a
four-distinct-zone qualification**.

**Temporary v0.4 development safeguard:** Native WMI RGB physical apply now
requires the editor slider to be **100%**. The GUI disables Apply/Restore
below 100 and explains how to dim with the actual Fn keys. The privileged
Python helper and kernel PoC independently **reject other brightness values**
before WMI writes. This is a deliberate guard based on tested hardware
behavior, **not** a statement that 100 is the only firmware-supported value.
Other native Linux LED and Linuwu sysfs backends retain their own behavior.
Do not silently overwrite a requested 65% with 100%; show the limitation.

If the green/100 condition ever causes unexpected colors, capture read-only
`probe=1` before and after and reopen the brightness hypothesis. Avoid
repeating known problematic green/65 firmware writes just to reproduce the
symptom. No automatic startup or EC writes are permitted.

See [issue #2](https://github.com/tobiasGuta/Nitro-Control/issues/2).
The November 1, 2026 [kernel RFC](https://lists.openwall.net/linux-kernel/2026/11/01/327)
describes independent EC hardware brightness (0..4) and WMI static-zone colors.
The [May 2026 RFC](https://lkml.iu.edu/2605.0/01657.html)
mentions keyboard brightness key events lacking release events on this model.
These are hypotheses to investigate, not verified explanations.

**Do not** mask the keyboard hotkeys, disable the Acer driver, issue raw EC
writes, or enable automatic RGB reinitialization to hide this regression.

### Safe read-only before/after hotkey check

With your work saved, stock `acer_wmi` running, and neither Linuwu nor
the temporary RGB module active, run this from the separate feature worktree.
The `probe=1` module invokes only firmware *get* methods (method 21 for mode
and brightness, method 7 for all four zone colors). Its **insertion** is not
the same as the firmware-writing `apply=1` command:

```bash
cd /mnt/Development/Tools/Nitro-Control-native-rgb/experiments/acer-wmi-rgb-enable-poc
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
systemctl is-active nitro-control-rgb-driver.service || true

# Read-only snapshot before Fn+F9
sudo insmod ./nitro_rgb_enable_poc.ko probe=1
sudo rmmod nitro_rgb_enable_poc
sudo journalctl -k -b --no-pager |
  grep nitro_rgb_enable_poc | tail -n 12

# Press Fn+F9 a few times, *one short press at a time*.
# Stop immediately when the colors visibly change.

# Read-only snapshot after Fn+F9
sudo insmod ./nitro_rgb_enable_poc.ko probe=1
sudo rmmod nitro_rgb_enable_poc
sudo journalctl -k -b --no-pager |
  grep nitro_rgb_enable_poc | tail -n 12
```

If insertion fails, do not continue. If removal fails, do not attempt another
insertion; inspect `lsmod` and the kernel journal. Compare the method-21
keyboard `mode`/`brightness` and method-7 `zone N raw` values. If these
change, the firmware state itself changed; if unchanged, the separate EC
brightness control or visual behavior is a candidate. The read-only probe
does **not** read EC global intensity, and a successful get call does not
prove visual illumination.


## Architecture and trust boundaries

```text
GTK RGB editor (normal user)
    | choose four #RRGGBB colors + brightness 100 ONLY for native WMI
    | explicit user confirmation
    v
RGBClient -> pkexec /usr/local/libexec/nitro-control-rgb-helper
    | fixed root-owned Python copy; validated JSON, model, WMI and Acer fans
    | sha256 + owner/mode + modinfo + running-kernel checks
    v
insmod /usr/lib/modules/$(uname -r)/extra/nitro-control/nitro_rgb_enable_poc.ko
       apply=1 colors=RRGGBB,RRGGBB,RRGGBB,RRGGBB brightness=100
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
3. Pick a simple uniform green (or known-working red) and set the editor
   brightness to **100%**; the native WMI buttons are unavailable below 100%
   due to the reported Fn hotkey/color regression.
4. Use **Restore lighting** or **Apply to keyboard** (never “Apply preview”).
   After success, adjust **physical** brightness with Fn+F9/Fn+F10; do not
   use the editor slider below 100 for native WMI initialization.
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
| Apply/Restore disabled below 100% | Native WMI temporary brightness guard | Set editor to 100%, Apply, then use Fn keys for actual dimming |
| Fn+F9 changes colors after a WMI apply | Issue #2: color/brightness interaction | Do not repeat 65%; compare read-only WMI states and use known-good 100% workflow |
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

- [x] Custom WMI module compiled on Fedora 45 kernel `7.2.15-300.fc45.x86_64` (2026-11-06).
- [x] Uniform green at WMI initialization brightness 100% visibly works; use Fn keys to dim afterward (2026-11-06).
- [ ] Four distinct zone colors visibly match the user's selections (not yet confirmed; multiple uniform colors have been tested).
- [x] Uniform green and another unspecified color at 100% retain normal Fn brightness behavior on the reference system (2026-11-06).
- [ ] Four independent zone colors retain normal Fn brightness behavior.
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
