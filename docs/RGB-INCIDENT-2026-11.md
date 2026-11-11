# AN515-58 RGB blackout investigation — November 2026

**Status:** The reference laptop is working following removal of the unused
legacy Linuwu Sense driver. One ordinary reboot and one suspend/resume after
cleanup **passed** the checks below. **The original reason the firmware's
RGB zone state cleared is still unproven.** Track future observations in
[issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).

## Reference configuration and scope

- **Hardware:** Acer Nitro AN515-58, four-zone RGB keyboard.
- **OS/kernel:** Fedora 45; `7.2.15-300.fc45.x86_64` was unchanged during
  the reported blackout and original recovery.
- **App:** Nitro Control `0.4.0-preview.1`, opt-in `native-wmi` backend.
- **Intended driver arrangement:** Keep upstream/stock `acer_wmi` loaded
  for fan telemetry and profile support. The separately installed
  `nitro_rgb_enable_poc.ko` is loaded only for an authorized one-shot
  RGB operation, then unloaded. **No automatic boot/resume WMI writes.**

This documents **one reference laptop**, not a general finding about every
Acer Nitro model, Fedora update, or Linuwu installation.

## User-visible failure and recovery

After a reported update-related blackout, the physical RGB keyboard was dark.
**Fn+F9 and Fn+F10 had no visible effect while the keyboard was dark.**
The native module was available for the **same running kernel**, so a kernel
version mismatch or missing current-kernel native build was **not** established.

In the affected November 10 boot journal, the native temporary module
logged the following at **21:32:47 EST**, before applying new RGB colors:

```text
keyboard state: return=0 mode=0 speed=0 brightness=100 direction=0 rgb=0,0,0
zone 1 raw=0x0000000000000000
zone 2 raw=0x0000000000000000
zone 3 raw=0x0000000000000000
zone 4 raw=0x0000000000000000
```

**Brightness was reported as 100, but all four firmware zone values were
zero.** This is stronger evidence of absent/cleared zone configuration than
of a simple globally zero brightness setting; it does **not** identify what
cleared the state. The module then accepted its guarded gaming-system poll,
WMI method 2 zone enable, method 20 static mode at brightness **100**,
and four method 6 zone colors. Method 7 readbacks matched. Separately, the
owner **physically verified** that Nitro Control's administrator-authorized
**Restore lighting** brought back the four RGB zones and normal Fn+F9/Fn+F10
brightness behavior. No module rebuild or reinstall was needed.

A previous native test at WMI initialization brightness **65** had caused
unwanted color changes when dimming; see [issue #2](https://github.com/tobiasGuta/Nitro-Control/issues/2).
The accepted, hardware-tested recovery path still requires **100% in the GUI
editor during physical Apply/Restore**, with Fn keys used for dimming afterward.

## Update and boot timeline (what the logs actually show)

| Local time | Evidence | Interpretation |
| --- | --- | --- |
| Nov 8, 15:42 | DNF5 transaction **105**, 23 upgraded packages / 46 altered entries, including `fwupd-efi`, GNOME Software, editors and libraries | **No kernel or `linux-firmware` upgrade** listed in this transaction; neither BIOS nor EC firmware was shown to be flashed |
| Nov 9, 03:20 | DNF transaction **106**, Tailscale installation | No direct RGB mechanism demonstrated |
| Nov 10, 20:02:29 | Both `acer_wmi` and **`linuwu_sense` Version 25.701** logged initialization | Unexpected legacy module activity despite its disabled, inactive systemd unit |
| Nov 10, 20:19:13 | System entered deep suspend | Zone state immediately beforehand not recorded |
| Nov 10, 21:02:48–49 | S3 resume with ACPI embedded-controller stop/start | Potential independent firmware reset point; effect on RGB not directly measured |
| Nov 10, 21:08:53 | `acer_wmi: Unknown function number - 9 - 0` | Meaning and relevance to RGB not established |
| Nov 10, 21:32:47 | Four firmware zone values zero; explicit native WMI Restore succeeded | Direct evidence of a cleared/uninitialized RGB state, **not** evidence identifying its original trigger |
| Nov 11, 02:24 | DNF transaction **107**, Zoom installation | **After** the November 10 recovery; cannot have triggered that earlier incident |

The supplied DNF history listed **no November 10 DNF transaction**.
`fwupdmgr get-history` reported **“No history”**; this is not proof
that firmware was never changed by some other mechanism.

## Why Linuwu loaded despite a disabled service

Read-only investigation confirmed:

- `nitro-control-rgb-driver.service`: **disabled**, **inactive**,
  and **no unit journal entries** in the affected boot.
- The legacy `linuwu_sense.ko` was still installed under
  `/usr/lib/modules/<kernel>/extra/nitro-control/`.
- `modinfo -F alias linuwu_sense` advertised
  `wmi:676AA15E-6A47-4D9F-A2CC-1E6D18D14026`.
- The reference laptop exposes that **exact WMI device modalias**.
- `modprobe -R 'wmi:676AA15E-6A47-4D9F-A2CC-1E6D18D14026'`
  resolved **both `acer_wmi` and `linuwu_sense`**.
- A privileged `lsinitrd` listing showed **no matching Linuwu file** in
  the inspected current-kernel initramfs.

This demonstrates an **alias-level module competition** and provides a
credible explanation for Linuwu being automatically discovered during
boot, independently of the disabled service. It does **not** establish
the exact loader process, simultaneous driver binding, or whether Linuwu
itself zeroed the four RGB zone values.

## Cleanup and its verified results

After confirming Linuwu was **not** currently loaded, the owner explicitly
ran Nitro Control's reviewed
`scripts/uninstall-rgb-driver-fedora.sh` and `sudo depmod -a`.
That guarded uninstaller removes **only the managed legacy Linuwu driver
copies and their digests plus its older service/controller**. It retains
stock `acer_wmi`, the separately installed native one-shot RGB module,
and the independent Polkit helper. The owner reported successful
completion.

After cleanup:

```text
modprobe -R 'wmi:676AA15E-6A47-4D9F-A2CC-1E6D18D14026'
acer_wmi

lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)'
acer_wmi               45056  0
```

A temporary `backend: none` result was **not** a native-driver failure.
The old experimental checkout in the terminal's working directory shadowed
the installed Python package. The owner reproduced the issue, then confirmed
the installed package worked from `~`. [PR #4](https://github.com/tobiasGuta/Nitro-Control/pull/4)
fixed the per-user launcher by changing into its installed directory and using
Python `-P`; automated regression tests passed and the owner verified the
merged fix **from inside that same older checkout**:

```text
Nitro Control 0.4.0-preview.1
"backend": "native-wmi"
"available": true
```

**Hardware validation after removing legacy Linuwu:**

| Test | Owner-confirmed result | Evidence limit |
| --- | --- | --- |
| Ordinary reboot and login | **PASS:** all four physical RGB zones lit; correct colors retained | One post-cleanup reboot, not long-term persistence |
| Fn+F9 / Fn+F10 after reboot | **PASS:** brightness controls behave normally | Physical user observation |
| Fans after reboot | **PASS:** fans operate normally | New numeric RPM values not supplied |
| Module/backend after reboot | **PASS:** only `acer_wmi` resident; `native-wmi` available | Backend probe is read-only, not independent physical light verification |
| Normal suspend/resume | **PASS:** four zone colors retained, Fn keys normal, fans operating normally | One post-cleanup sleep/wake cycle |

No manual **Restore lighting** was reported as necessary for the
post-cleanup reboot or sleep/wake tests.

## Safe monitoring and next steps

The cleanup and two successful real-world tests are **encouraging**, but
do **not prove** the old driver caused the original failure; an EC state
reset at suspend/resume or an unrelated firmware action remains possible.
Keep [issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3)
open until future ordinary updates, cold boots, and sleep/wake cycles
supply enough evidence to narrow the trigger.

If RGB becomes dark again, **capture logs and the exact local time before
clicking Restore**:

```bash
date --iso-8601=seconds
~/.local/bin/nitro-control --rgb-probe
lsmod | grep -E '^(acer_wmi|linuwu_sense|nitro_rgb_enable_poc)' || true
journalctl -k -b --no-pager |
    grep -Ei 'acer_wmi|linuwu|nitro_rgb|ACPI: EC|PM: suspend' |
    tail -n 100
```

Then, if needed, use the existing **explicitly authorized, manual**
Restore lighting operation with editor brightness **100%**. Do not
blindly rebuild modules, reload conflicting drivers, downgrade unrelated
packages, adjust fan PWM, or add unattended firmware writes.

Related: [native RGB architecture and safety guide](NATIVE-RGB.md),
[preview release notes](RELEASE-v0.4-preview.1.md),
[unexpected 65%-brightness Fn interaction, issue #2](https://github.com/tobiasGuta/Nitro-Control/issues/2),
and [November RGB incident, issue #3](https://github.com/tobiasGuta/Nitro-Control/issues/3).
