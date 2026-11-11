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

**Four-zone hardware validation, November 6, 2026:** The owner confirms
that a **four-different-color configuration** was physically applied through
Nitro-Control v0.4 **at WMI initialization brightness 100**, that the four
zone colors appeared as expected, and that Fn+F9/Fn+F10 continued working
normally without unexpected color changes. The provided GUI screenshot shows
the *Midnight* editor palette:

- Zone 1: `#5931B5`
- Zone 2: `#4536B6`
- Zone 3: `#245A9B`
- Zone 4: `#16869B`

The screenshot itself shows the editor's *current draft* at 65% with physical
Apply disabled; it is **not evidence of the brightness sent previously**.
The earlier user-reported physical four-zone test was at 100%.
This completes the manual four-different-zone and brightness-hotkey test on
this single Fedora 45 machine, **not** suspend/resume, reboot, or support
for untested WMI brightness values.

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
| RGB dark after Fedora updates, but `native-wmi` still available | `uname -r`, `lsmod`, `--rgb-probe` | Fn+F9/Fn+F10 may do nothing in this state (confirmed Nov 10); use authorized **Restore lighting** at editor brightness **100%**. No rebuild when the current-kernel backend is already valid |
| Apply/Restore disabled below 100% | Native WMI temporary brightness guard | Set editor to 100%, Apply, then use Fn keys for actual dimming |
| Fn+F9 changes colors after a WMI apply | Issue #2: color/brightness interaction | Do not repeat 65%; compare read-only WMI states and use known-good 100% workflow |
| `rmmod` failure | `lsmod` and kernel journal | Stop further tests; recover or reboot to stock driver |

### November 10, 2026: successful recovery after Fedora updates

The owner reported that keyboard RGB went dark after installing updates.
Read-only diagnostics confirmed the same previously tested kernel
(`7.2.15-300.fc45.x86_64`), stock `acer_wmi` loaded, a correctly located
native PoC module and digest for that kernel, `--rgb-probe` returning
`native-wmi` / `available: true`, and the power profile `low-power`.
No Linuwu or native PoC module was resident. **No kernel mismatch was
demonstrated**; `low-power` was observed but was not shown to cause the
lighting issue.

**The owner clarified the decisive recovery step:** While the keyboard
was dark, pressing **Fn+F9 and Fn+F10 had no effect**. The lighting returned
only after opening Nitro-Control, setting the desired four colors with the
editor slider at **100%**, and using the authorized **Restore lighting**
operation. **No module rebuild or reinstall was needed.** The GUI's native
recovery executes the guarded Acer WMI initialization sequence; this supports
a firmware RGB initialization/state issue over a simple Fn brightness-at-zero
explanation. It does **not** prove which WMI method or prior update event
caused the blackout.

**Follow-up confirmation:** After the authorized 100%-initialization
Restore, the owner confirms **Fn+F9 and Fn+F10 again adjust the keyboard
brightness normally without changing the selected RGB colors**. In contrast,
both keys were ineffective while the keyboard was dark. Fresh fan RPM
measurements after this specific recovery were not reported; earlier
checks had confirmed functioning Acer fan telemetry.

This is confirmation that the **manual recovery workflow works on this
existing kernel**, not proof of a particular firmware reset cause, repeatable
long-term persistence after every update, or post-recovery fan RPM measurements.
Do not add unattended firmware writes or boot services on this evidence.
Track recurrence and investigation in
[issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).

### November 11 investigation: package history and legacy module at boot

Further read-only evidence narrows the November 10 blackout, but **the
root cause is still unknown**:

- The most recent multi-package DNF transaction (#105, November 8)
  upgraded GNOME Software, `fwupd-efi`, and general desktop/application
  packages. It did **not** include a kernel or `linux-firmware` upgrade.
  Upgrading the `fwupd-efi` package is **not** the same as flashing Acer
  BIOS or EC firmware; `fwupdmgr get-history` reported no firmware history.
- The November 10 boot journal positively records **Linuwu Sense 25.701
  module initialization at 20:02:29**, with out-of-tree/signature-taint
  warnings, **alongside stock `acer_wmi` initialization**. Later `lsmod`
  showed only `acer_wmi`. This is a possible driver/module lifecycle lead,
  not proof of the cause. Inspect its autoload path and whether it was
  unloaded before the keyboard went dark.
- A deep ACPI S3 suspend occurred at 20:19:13, followed by EC restart and
  resume at 21:02:48. At 21:08:53 the kernel logged
  `acer_wmi: Unknown function number - 9 - 0`, which has no demonstrated
  causal connection to RGB.
- At 21:32:47, **before the authorized Restore firmware writes**, the
  native module read keyboard brightness 100 and all four raw RGB zone
  values zero. Method 2/20/6 restoration and readback then succeeded;
  the user confirmed physical illumination and Fn+F9/Fn+F10 afterward.

Avoid blaming DNF package upgrades, Linuwu, or suspend individually without
isolating the time of first blackout. Do **not** reproduce by deliberately
mixing drivers or changing ACPI/EC settings. Use read-only systemd unit
and modules-load configuration inspection first; the investigation and
diagnostic commands are tracked in
[issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).

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
- [x] Four distinct zone colors visibly match the selected Midnight palette after 100% initialization on the reference AN515-58 (user-confirmed 2026-11-06).
- [x] Uniform green at 100% retains normal Fn brightness behavior on the reference system (2026-11-06).
- [x] Four independent zone colors retain normal Fn brightness behavior after 100% initialization (user-confirmed 2026-11-06).
- [x] Stock `acer_wmi` and `balanced` platform profile were observed after native apply and after reboot; Acer fan1/fan2 telemetry read 2272/2222 RPM after apply; user also confirmed fans physically working (2026-11-06). This does not validate manual fan PWM control.
- [ ] A denied Polkit prompt results in no physical change.
- [ ] An invalid/missing module or digest never initiates a WMI call.
- [ ] No one-shot module remains loaded after success or failure.
- [x] Keyboard illumination/color configuration **survived suspend and resume** on Fedora 45 (user-confirmed 2026-11-06).
- [ ] After resume, independently recheck Fn+F9/Fn+F10, stock `acer_wmi`, fan1/fan2 telemetry and platform profile.
- [x] Full reboot: the previously applied four-zone colors were **physically present after logging into Fedora 45** (user-confirmed 2026-11-06), without running the manual RGB recovery.
- [x] Same four-zone colors were already illuminated **before GDM/login appeared**, and remained unchanged through login (user-confirmed 2026-11-06).
- [ ] Confirm reboot behavior on future kernels and over repeated cold starts.

### Suspend/resume: first successful user report (November 6, 2026)

The AN515-58 owner reports that the **previously applied native RGB
keyboard lighting survives a normal suspend/resume cycle**. This is an
observed physical result, not merely a successful firmware readback. The
report does not independently establish post-resume fan sensor values or
Fn-key operation; those remain separate checks. Do not infer repeated-cycle
reliability from a single successful report.

### Fan preservation (November 6, 2026)

The owner separately confirmed that the laptop's fans are operating normally
while the native Acer RGB backend and stock `acer_wmi` are used. Earlier
post-apply checks read `fan1=2272 RPM` and `fan2=2222 RPM` from Acer
HWMON, with the firmware profile `balanced`. This supports preservation of
fan operation and telemetry. It does **not** prove that Nitro-Control controls
manual fan PWM or fan curves; this RGB backend does not write either.

### Reboot/login result (November 6, 2026)

**Passed for both pre-login and post-login on the reference AN515-58,
Fedora 45, kernel `7.2.15-300.fc45.x86_64`.** After a full reboot, the
owner observed that the previously selected four-zone colors were already
physically illuminated **before the login page appeared**, and remained
the same after login. No manual recovery step was run before reporting this
result. Post-reboot command outputs showed `acer_wmi` loaded, no Linuwu or
temporary PoC module loaded, `native-wmi` capability available (this is
only a probe, not a hardware write), `platform_profile=balanced`, and the
old `nitro-control-rgb-driver.service` remained disabled.

**Scope:** This confirms physical RGB persistence through one reboot,
including before the GDM login page and after entering the desktop. It does
**not** establish behavior on every future cold boot, across kernel upgrades,
or whether the firmware itself versus some early-boot component retains or
restores the colors. Do not credit a new Nitro-Control startup service; none
was added in this branch. No unattended startup recovery is currently needed.

The following checks remain useful if the behavior later regresses or on a
future kernel upgrade:

### Optional controlled reboot/login inspection

Save all work before rebooting. Keep the legacy
`nitro-control-rgb-driver.service` **disabled** and do not add any native
RGB boot service. After restarting, observe the keyboard *before* login
and *after* the desktop appears, **without pressing Apply first**. Record
whether the RGB color configuration remains and whether Fn+F9/Fn+F10
controls physical brightness. Then check:

```bash
uname -r
systemctl is-enabled nitro-control-rgb-driver.service
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
cat /sys/firmware/acpi/platform_profile
for d in /sys/class/hwmon/hwmon*; do
  if [ "$(cat "$d/name" 2>/dev/null)" = acer ]; then
    printf '%s fan1=%s fan2=%s\n' "$d" \
      "$(cat "$d/fan1_input" 2>/dev/null)" \
      "$(cat "$d/fan2_input" 2>/dev/null)"
  fi
done
~/.local/bin/nitro-control --rgb-probe
```

If the keyboard is dark, open Nitro Control and use the authorized
**Restore lighting** operation with editor brightness **100%** to test
whether native initialization recovers it; this does not require the
Linuwu driver. If the updated app/helper or pinned module is unavailable
after a kernel update, **do not force-load a mismatched module**; rebuild
and reinstall for the new running kernel after reviewing the source.
The original known-good red-only `restore-after-boot.sh` is an
alternative manual fallback **only** when compiled for that exact kernel.

Record physical results separately from the software probe. **Do not
install a boot-recovery service when the four-zone configuration already
survives pre-login GDM and login normally.**

**No automatic startup/resume restore is included in this preview.** Keep
boot autoload disabled. There is no guarantee that a new kernel will be
compatible. Revisit the startup design only if a real, repeatable failure
is observed and reviewed.

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
